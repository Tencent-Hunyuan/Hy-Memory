"""
HY Memory - MemoryInspector 调试/审计工具类

提供对记忆系统内部状态的查询能力，包括：
- 调用链日志（EXTRACT / SEARCH_QUERY / RECONCILE 的 prompt 和 response）
- 知识库变动记录（ADD / UPDATE 操作历史）

同时提供同步和异步两种接口，适配不同调用场景：
- 同步版（get_xxx）：通过 _loop_thread 在后台 loop 执行，适合普通脚本 / CLI
- 异步版（async_get_xxx）：安全委托到 _LoopThread 的 event loop，适合任意 async 框架

用法:
    from hy_memory import HyMemoryClient, MemoryInspector

    client = HyMemoryClient()
    inspector = MemoryInspector(client)

    # 同步
    logs = inspector.get_pipeline_logs(request_id="abc123")
    trace = inspector.get_full_trace("abc123")

    # 异步（在 async 上下文中）
    logs = await inspector.async_get_pipeline_logs(request_id="abc123")
    trace = await inspector.async_get_full_trace("abc123")
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from .utils.async_executor import await_concurrent_future_guarded


def _log_time_key(log: Dict[str, Any]) -> tuple:
    """把一条 pipeline log 排到它「发生的时刻」上。

    ``event_at_ms`` 是 step 发生时刻，但它对补列之前写入的历史行是 0（见
    ``cache_sqlite._migrate_pipeline_logs_columns``）。直接拿 0 当键会让这些行
    排到所有新行**前面**，与「缺省回落 created_at」的约定正好相反 —— 一次
    search 的 trace 里 READ_REQUEST 会跑到 RECALL 后面几步的后面去。

    因此：有 event_at_ms 用它；没有就解析 created_at；两者都拿不到时排到最后，
    保持入参顺序稳定（list.sort 是稳定排序）。
    """
    event_ms = log.get("event_at_ms") or 0
    if event_ms:
        return (float(event_ms), "")
    created = log.get("created_at")
    if created:
        try:
            if isinstance(created, datetime):
                moment = created
            else:
                # Python 3.9/3.10 的 fromisoformat 只认 "YYYY-MM-DDTHH:MM:SS"：
                # 不认 "Z" 后缀、也不认空格分隔。这两种写法在外部写入的历史
                # 行里都会出现，直接交给它会让整组行退化成「解析失败」。
                moment = datetime.fromisoformat(
                    str(created).replace("Z", "+00:00").replace(" ", "T", 1)
                )
            return (moment.timestamp() * 1000.0, str(created))
        except (TypeError, ValueError):
            pass
    # 键的第二项统一成字符串：缺失值若留 None，与别的行的 "" 比较会抛
    # TypeError（sort 直接崩），而它们本来就该并列排最后。
    return (float("inf"), str(created))


class MemoryInspector:
    """
    记忆系统调试/审计工具。

    从 HyMemoryClient 实例获取内部 cache 引用，
    同步方法通过 _loop_thread 确保在正确的 event loop 上执行，
    异步方法直接 await cache 操作。
    """

    def __init__(self, client):
        """
        Args:
            client: HyMemoryClient 实例
        """
        self._client = client
        self._cache = client._cache
        self._loop_thread = client._loop_thread

    async def _run_on_internal_loop(self, coro):
        """
        确保协程在 _LoopThread 的 event loop 上执行。
        如果当前已在正确的 loop 上，直接 await；否则委托。
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        internal_loop = self._loop_thread._loop

        if current_loop is internal_loop:
            return await coro
        else:
            future = asyncio.run_coroutine_threadsafe(coro, internal_loop)
            return await await_concurrent_future_guarded(future, internal_loop)

    # ================================================================
    # Pipeline Logs（LLM 调用链中间结果）
    # ================================================================

    def get_pipeline_logs(
        self,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        step: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询 pipeline LLM 调用链中间结果（同步）"""
        return self._loop_thread.run(
            self.async_get_pipeline_logs(
                request_id=request_id, user_id=user_id, step=step, limit=limit,
            )
        )

    async def async_get_pipeline_logs(
        self,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        step: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询 pipeline LLM 调用链中间结果（异步）。

        每次 add() 调用，lite pipeline 的 extract / search_query / reconcile
        三个步骤的 prompt、response、解析结果都会写入持久化存储。

        Args:
            request_id: 按 request_id 过滤（查某次写入的完整调用链）
            user_id:    按 user_id 过滤
            step:       按步骤过滤（"EXTRACT" / "SEARCH_QUERY" / "RECONCILE"）
            limit:      最多返回条数

        Returns:
            [{"step", "prompt", "response", "parsed", "memory_ids",
              "elapsed_ms", "created_at", "request_id", "user_id", "agent_id"}]
        """
        return await self._run_on_internal_loop(
            self._cache.get_pipeline_logs(
                request_id=request_id, user_id=user_id, step=step, limit=limit,
            )
        )

    # 快捷方法（同步）

    def get_extract_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 EXTRACT 步骤日志"""
        logs = self.get_pipeline_logs(request_id=request_id, step="EXTRACT", limit=1)
        return logs[0] if logs else None

    def get_search_query_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 SEARCH_QUERY 步骤日志"""
        logs = self.get_pipeline_logs(request_id=request_id, step="SEARCH_QUERY", limit=1)
        return logs[0] if logs else None

    def get_reconcile_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 RECONCILE 步骤日志"""
        logs = self.get_pipeline_logs(request_id=request_id, step="RECONCILE", limit=1)
        return logs[0] if logs else None

    # 快捷方法（异步）

    async def async_get_extract_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 EXTRACT 步骤日志（异步）"""
        logs = await self.async_get_pipeline_logs(request_id=request_id, step="EXTRACT", limit=1)
        return logs[0] if logs else None

    async def async_get_search_query_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 SEARCH_QUERY 步骤日志（异步）"""
        logs = await self.async_get_pipeline_logs(request_id=request_id, step="SEARCH_QUERY", limit=1)
        return logs[0] if logs else None

    async def async_get_reconcile_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次请求的 RECONCILE 步骤日志（异步）"""
        logs = await self.async_get_pipeline_logs(request_id=request_id, step="RECONCILE", limit=1)
        return logs[0] if logs else None

    # ================================================================
    # Memory Operations（知识库变动记录）
    # ================================================================

    def get_memory_operations(
        self,
        request_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询知识库变动记录（同步）"""
        return self._loop_thread.run(
            self.async_get_memory_operations(
                request_id=request_id, memory_id=memory_id, user_id=user_id, limit=limit,
            )
        )

    async def async_get_memory_operations(
        self,
        request_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询知识库变动记录（异步）。

        每次 reconcile 产生的 ADD / UPDATE 操作都会写入持久化存储。

        Args:
            request_id: 查某次写入触发了哪些知识变动
            memory_id:  查某条记忆是怎么来的（ADD 还是 UPDATE）
            user_id:    查某个用户的全部变动历史
            limit:      最多返回条数

        Returns:
            [{"op", "memory_id", "old_memory_id", "content", "layer",
              "reason", "created_at", "request_id", "user_id", "agent_id"}]
        """
        return await self._run_on_internal_loop(
            self._cache.get_memory_operations(
                request_id=request_id, memory_id=memory_id, user_id=user_id, limit=limit,
            )
        )

    # ================================================================
    # 综合查询（一次拿到完整链路）
    # ================================================================

    def get_full_trace(self, request_id: str) -> Dict[str, Any]:
        """获取某次写入的完整追踪信息（同步）"""
        return self._loop_thread.run(
            self.async_get_full_trace(request_id)
        )

    async def async_get_full_trace(self, request_id: str) -> Dict[str, Any]:
        """
        获取某次写入的完整追踪信息（异步）。

        一次调用拿到调用链 + 知识变动。

        Args:
            request_id: add() 返回的 request_id

        Returns:
            {
                "request_id": "...",
                "pipeline_logs": [EXTRACT, SEARCH_QUERY, RECONCILE],
                "memory_operations": [ADD, UPDATE, ...],
            }
        """
        import asyncio
        logs, ops = await asyncio.gather(
            self.async_get_pipeline_logs(request_id=request_id),
            self.async_get_memory_operations(request_id=request_id),
        )
        return {
            "request_id": request_id,
            "pipeline_logs": logs,
            "memory_operations": ops,
        }

    # ================================================================
    # Search / Read 专用追踪（post15+）
    # ================================================================

    # 约定所有 read-side step 字符串以此前缀开始，inspector 用它做过滤
    _READ_STEP_PREFIX = "READ_"

    # 每套 reader 写入的典型步骤序列，供 UI 按序渲染
    READ_STEP_ORDER = [
        "READ_REQUEST",
        "READ_EMBED_QUERY",
        "READ_RECALL_VEC",
        "READ_HIDDEN_L0_CONTEXT",
        "READ_BM25",
        "READ_GRAPH_RERANK",
        "READ_MERGE_SCHEMA",
        "READ_EVOLUTION",
        "READ_SUMMARY",
    ]
    def get_read_trace(self, request_id: str) -> Dict[str, Any]:
        """查询某次 search 的完整 read pipeline trace（同步）。"""
        return self._loop_thread.run(self.async_get_read_trace(request_id))

    async def async_get_read_trace(self, request_id: str) -> Dict[str, Any]:
        """
        查询某次 search 的完整 read pipeline trace（异步）。

        从 `pipeline_logs` 表拉所有属于这个 request_id 的 `READ_*` 步骤，按步骤名
        分组后返回。也顺带返回一个"推荐渲染顺序"供 UI 排序。

        Args:
            request_id: `client.search()` / `client.async_search()` 返回的 request_id

        Returns:
            {
                "request_id": "...",
                "reader": "hybrid" / "legacy" / None,
                "steps": [  # 按 created_at 升序
                    {"step", "prompt", "response", "parsed" (已 parse 成 dict),
                     "memory_ids", "elapsed_ms", "created_at"}
                ],
                "by_step": { "READ_RECALL_VEC": [...], ... },
                "recommended_order": [...],   # 根据 reader 版本返回典型步骤序列
                "summary": { ... } | None,    # READ_SUMMARY 这一条的 parsed 快捷
            }
        """
        all_logs = await self.async_get_pipeline_logs(request_id=request_id, limit=500)
        read_logs = [log for log in all_logs if (log.get("step") or "").startswith(self._READ_STEP_PREFIX)]

        # 按 event_at_ms 升序（step 发生时刻，最准）；缺省回落 created_at 保持稳定
        read_logs.sort(key=_log_time_key)

        import json as _json
        parsed_logs: List[Dict[str, Any]] = []
        by_step: Dict[str, List[Dict[str, Any]]] = {}
        reader_version = None
        summary_parsed: Optional[Dict[str, Any]] = None

        for log in read_logs:
            item = dict(log)
            # 把 parsed 字段反序列化一下，UI 能直接读
            raw_parsed = log.get("parsed")
            if isinstance(raw_parsed, str) and raw_parsed:
                try:
                    item["parsed"] = _json.loads(raw_parsed)
                except Exception:
                    item["parsed"] = raw_parsed
            # 从任一 log 抓 reader 标签
            if reader_version is None and isinstance(item.get("parsed"), dict):
                reader_version = item["parsed"].get("_reader") or reader_version
            parsed_logs.append(item)
            by_step.setdefault(item.get("step", ""), []).append(item)
            if item.get("step") == "READ_SUMMARY" and isinstance(item.get("parsed"), dict):
                summary_parsed = item["parsed"]

        # 推荐顺序
        order = self.READ_STEP_ORDER

        return {
            "request_id": request_id,
            "reader": reader_version,
            "steps": parsed_logs,
            "by_step": by_step,
            "recommended_order": order,
            "summary": summary_parsed,
        }

    # 快捷方法：对应单步读取
    def get_read_summary(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次 search 的 READ_SUMMARY 总览日志（同步）。"""
        return self._loop_thread.run(self.async_get_read_summary(request_id))

    async def async_get_read_summary(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取某次 search 的 READ_SUMMARY 总览日志（异步）。"""
        logs = await self.async_get_pipeline_logs(request_id=request_id, step="READ_SUMMARY", limit=1)
        return logs[0] if logs else None

    # ================================================================
    # 统计（论文 / 分析用）
    # ================================================================

    @staticmethod
    def _classify_op(op_record: Dict[str, Any]) -> str:
        """
        统一分类 op 类型。

        兼容旧版本：旧版 supersede 存储为 op="ADD" + supersedes=[target_id]（非空），
        需要重新分类为 "SUPERSEDE"。
        """
        op_type = op_record.get("op", "")
        # 旧版兼容：op=ADD 但 supersedes 非空 → 实际是 SUPERSEDE
        if op_type == "ADD":
            supersedes = op_record.get("supersedes", [])
            if supersedes and len(supersedes) > 0:
                return "SUPERSEDE"
        return op_type

    def get_evolve_stats(self, request_id: str) -> Dict[str, Any]:
        """
        获取单次 evolve（或 add 调用）的操作统计。

        Returns:
            {
                "request_id": "...",
                "op_counts": {"ADD": N, "SUPERSEDE": N, "UPDATE": N, ...},
                "net_change": N,       # add + supersede - delete（memory 净增量）
            }
        """
        ops = self.get_memory_operations(request_id=request_id)
        from collections import Counter
        op_counts = Counter(self._classify_op(o) for o in ops)

        # net_change: 产生新节点的 op - 删除的 op
        _ADD_OPS = {"ADD", "SUPERSEDE", "GRAPH_ADD", "SWEEPER_CORE_CREATE"}
        _DEL_OPS = {"DELETE", "GRAPH_DELETE"}
        net = sum(op_counts.get(k, 0) for k in _ADD_OPS) - sum(op_counts.get(k, 0) for k in _DEL_OPS)

        return {
            "request_id": request_id,
            "op_counts": dict(op_counts),
            "net_change": net,
        }

    def get_user_stats(
        self,
        user_id: str,
        limit: int = 10000,
    ) -> Dict[str, Any]:
        """
        获取用户维度的累计操作统计（按 op 类型独立列出）。

        兼容旧版本：旧版 supersede 存为 op="ADD" + supersedes 非空，
        统计时自动归为 SUPERSEDE。

        Returns:
            {
                "user_id": "...",
                "total_ops": N,
                "op_counts": {"ADD": N, "SUPERSEDE": N, "UPDATE": N, "GRAPH_ADD": N, ...},
                "total_net_change": N,      # 净增量 (ADD+SUPERSEDE+GRAPH_ADD+SWEEPER_CORE_CREATE) - (DELETE+GRAPH_DELETE)
                "total_requests": N,         # 不同 request_id 数量
                "per_request": [             # 每次 write/evolve 的统计（按时间排序）
                    {"request_id": "...", "op_counts": {...}, "net": N, "created_at": "..."},
                    ...
                ]
            }
        """
        ops = self.get_memory_operations(user_id=user_id, limit=limit)

        from collections import OrderedDict, Counter

        # 按 request_id 分组
        request_map: OrderedDict = OrderedDict()
        for o in reversed(ops):  # ops 是 DESC，reverse 变 ASC
            rid = o.get("request_id", "")
            if rid not in request_map:
                request_map[rid] = {"op_counts": Counter(), "created_at": o.get("created_at", "")}
            op_type = self._classify_op(o)
            request_map[rid]["op_counts"][op_type] += 1

        # 汇总
        total_counter = Counter()
        for d in request_map.values():
            total_counter.update(d["op_counts"])

        _ADD_OPS = {"ADD", "SUPERSEDE", "GRAPH_ADD", "SWEEPER_CORE_CREATE"}
        _DEL_OPS = {"DELETE", "GRAPH_DELETE"}

        per_request = []
        for rid, d in request_map.items():
            oc = d["op_counts"]
            net = sum(oc.get(k, 0) for k in _ADD_OPS) - sum(oc.get(k, 0) for k in _DEL_OPS)
            per_request.append({
                "request_id": rid,
                "op_counts": dict(oc),
                "net": net,
                "created_at": d["created_at"],
            })

        total_net = sum(total_counter.get(k, 0) for k in _ADD_OPS) - sum(total_counter.get(k, 0) for k in _DEL_OPS)

        return {
            "user_id": user_id,
            "total_ops": sum(total_counter.values()),
            "op_counts": dict(total_counter),
            "total_net_change": total_net,
            "total_requests": len(request_map),
            "per_request": per_request,
        }


    # ================================================================
    # System Metrics
    # ================================================================

    def get_system_metrics(self, minutes: int = 5) -> Dict[str, Any]:
        """
        获取系统级负载指标（默认最近5分钟）。

        Returns:
            {
                "uptime_seconds", "requests", "avg_latency_ms",
                "throughput", "storage_ops", "sys2_requests", "since"
            }
        """
        from .metrics import MetricsCollector
        return self._loop_thread.run(MetricsCollector.get().get_snapshot(minutes=minutes))

    async def async_get_system_metrics(self, minutes: int = 5) -> Dict[str, Any]:
        """获取系统级负载指标（异步，默认最近5分钟）"""
        from .metrics import MetricsCollector
        return await self._run_on_internal_loop(
            MetricsCollector.get().get_snapshot(minutes=minutes)
        )
