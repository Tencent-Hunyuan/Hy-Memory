# -*- coding: utf-8 -*-
"""
HY Memory Client - 面向用户的同步客户端（无状态）

所有组件在构造函数中完成初始化，失败立即报错。
构造函数只接受连接配置，不绑定任何业务参数（user_id/agent_id 等），
一个 client 实例可被多线程共享。

    # 最简用法 — 环境变量配置
    client = HyMemoryClient()
    client.add("用户喜欢科幻电影", user_id="test_user")
    results = client.search("用户喜欢什么？", user_ids=["test_user"])
    client.close()

    # 自定义配置（类似 mem0 风格）
    client = HyMemoryClient.from_config({
        "embedder": {"provider": "openai", "model": "text-embedding-3-small"},
        "vector_store": {"provider": "qdrant", "collection_name": "my_memories"},
        "graph_store": {
            "provider": "neo4j",
            "url": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "password",
        },
        "enable_graph": True,
    })
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Union, TYPE_CHECKING

from .config import MemoryConfig
from .core.embed_service import EmbedService, embed_endpoint_override
from .data.vector_store import create_vector_store
from .data.history_store import HistoryStore
from .data.cache import create_cache
from .utils.log_setup import setup_logging, set_request_id, request_id_scope
from .utils.async_executor import (
    await_concurrent_future_guarded,
    concurrent_result_guarded,
)

if TYPE_CHECKING:
    from .runtime import SharedRuntime
from .pipelines import (
    ComponentFactory,
    ChatMessage,
    ToolCall,
    WriteRequest,
    WriteResponse,
    ReadRequest,
    ReadResponse,
)
# Productivity memory 模块（含 tool 时三分类为 working/coding 才启用）
from .coding.preproc import has_any_tool_message, strip_tool_messages
from .coding.judge import classify_messages_scene

logger = logging.getLogger(__name__)

# add() 第一个参数的类型：字符串 或 messages 列表
MessageList = List[Dict[str, str]]
AddInput = Union[str, MessageList]

# working（coding）路两层的默认阈值。刻意不复用 chat 路的 min_score：
# chat 匹配的是完整语义单元，0.4 是长期在用的经验值；working 事实层匹配的是一句话
# 结论，同样语义相关度下分数系统性偏低，0.4
# 会把明确相关的事实挡掉。而事实层是主索引，它被挡掉不只是少一条事实：顺
# derived_experience_ids 带经验那一路会跟着断源，双层结构对该 query 直接退化成一次
# 普通经验搜索。所以事实层宁松勿严，噪声交给经验直查那道闸门单独收。
_WORKING_FACT_DEFAULT_MIN_SCORE = 0.30

# 经验直查阈值相对事实层阈值的默认抬升量。
# 两层 embed 的文本形态不同——事实层是整句 statement，经验层是 task + search_keys
# 这类短片段——短片段之间的余弦相似度基线本来就更高，同一个阈值下经验层会明显更松。
# 两层相关/无关的可分区间错开约 0.1，共用一个阈值罩不住。
_WORKING_EXPERIENCE_MIN_SCORE_DELTA = 0.10

# 一次 get_many 最多带多少个 experience_id。SQLite 的 IN 子句有绑定变量上限
# （默认 999），这是防病态长链的安全阀，不是返回条数控制。
_CODING_GET_MANY_MAX_IDS = 500


_WRITE_DIAGNOSTIC_KEYS = (
    "agent_status",
    "agent_error_code",
    "agent_error",
    "l1_rollback",
    "l1_rollback_failed",
    "partial_agent_persist",
    "session_episode",
    "workload",
    "graph_executor",
)


def _attach_write_diagnostics(resp: Dict[str, Any], extra: Dict[str, Any]) -> None:
    """Expose retry-safety details produced by the write pipeline."""
    for key in _WRITE_DIAGNOSTIC_KEYS:
        if key in extra:
            resp[key] = extra[key]


def _search_memory_channel(
    memory: Dict[str, Any],
    *,
    schema_layers: set[str],
    proactive_layers: set[str],
    schema_min_score: float,
) -> str:
    """Return the public search channel and consume private routing markers."""
    if bool(memory.pop("_schema_channel", False)):
        return "schema"
    layer = memory.get("layer", "")
    if layer in schema_layers:
        return "schema" if memory.get("score", 0.0) >= schema_min_score else "normal"
    if layer in proactive_layers:
        return "proactive"
    return "normal"


# cgroup CPU 限流累计时长的文件位置（v2 优先，回退 v1）。
# (路径, 字段名, 除数)——v1 的 throttled_time 单位是纳秒，需 /1000 换成微秒。
_CGROUP_CPU_STAT_SOURCES = (
    ("/sys/fs/cgroup/cpu.stat", "throttled_usec", 1),
    ("/sys/fs/cgroup/cpu/cpu.stat", "throttled_time", 1000),
)


def _read_throttled_usec() -> int:
    """读 cgroup 累计被 CPU 限流的微秒数；读不到返回 0。

    用于归因进程级停顿：在一段纯同步代码前后各取一次，差值即该段内被容器
    强制暂停的时长。容器 CPU quota 打满时，进程会被内核整体挂起，表现为
    "纯同步代码耗时几百毫秒"。

    同步读小文件（procfs/cgroupfs，无磁盘 IO），微秒级，不阻塞 event loop。
    任何异常都静默返回 0——这是可观测性字段，绝不能影响主流程。

    注：字段用**精确等值**匹配而非前缀匹配。cgroup v1 的 cpu.stat 里同时存在
    `throttled_time` 与 `throttled_time_bt`（burst 统计），前缀匹配会因文件内
    字段顺序不同而取错值。
    """
    for path, key, div in _CGROUP_CPU_STAT_SOURCES:
        try:
            with open(path) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == key:
                        return int(parts[1]) // div
        except Exception:
            continue
    return 0


def _gc_collection_count() -> int:
    """GC 各代累计回收次数之和；异常返回 0。

    与 _read_throttled_usec 配对使用：前后差值 >=1 说明该段内发生过 GC，
    可解释纯同步代码的意外停顿（高对象分配率下 gen2 GC 可达数百毫秒）。
    """
    try:
        import gc
        return sum(s.get("collections", 0) for s in gc.get_stats())
    except Exception:
        return 0


def _ensure_internal_loop(method):
    """
    Decorator for async_xxx() methods on HyMemoryClient.

    Ensures the coroutine runs on _LoopThread's event loop, not the caller's.
    If already on the correct loop, executes directly.
    If on an external loop (e.g., uvicorn), delegates to _LoopThread via run_coroutine_threadsafe.
    """
    import functools

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        submitted_at = time.perf_counter()

        async def _invoke():
            started_at = time.perf_counter()
            result = await method(self, *args, **kwargs)
            if isinstance(result, dict) and method.__name__ in {
                "async_add", "async_add_extracted", "async_finalize_session",
            }:
                runtime_diag = result.setdefault("runtime", {})
                runtime_diag["submit_wait_ms"] = round(
                    (started_at - submitted_at) * 1000, 3,
                )
                lag_snapshot = getattr(self._loop_thread, "lag_snapshot", None)
                if callable(lag_snapshot):
                    runtime_diag["event_loop"] = lag_snapshot()
            return result

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        internal_loop = self._loop_thread._loop

        if current_loop is internal_loop:
            # Already on _LoopThread's loop — execute directly
            return await _invoke()
        else:
            # On external loop (uvicorn, etc.) — delegate to _LoopThread
            future = asyncio.run_coroutine_threadsafe(
                _invoke(), internal_loop
            )
            return await await_concurrent_future_guarded(future, internal_loop)

    return wrapper


def _ensure_search_loop(method):
    """
    Decorator for async_search() on HyMemoryClient.

    与 _ensure_internal_loop 同构，但把协程绑定到 **search 专用 loop**
    (self._search_loop_thread) 而非 write loop，使 search 的调度不被 write
    队列（EXTRACT/RECONCILE/write_queue 并发）挤占。
    search 与 write 使用独立 loop，避免写队列占用 search 调度。

    solo 模式下 _search_loop_thread 可能与 _loop_thread 是同一个（不隔离），
    行为等价于旧路径；shared 模式（SharedRuntime）下二者是两个独立线程。
    """
    import functools

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        search_loop = self._search_loop_thread._loop

        if current_loop is search_loop:
            # 已经在 search loop 上 — 直接执行
            return await method(self, *args, **kwargs)
        else:
            # 外部 loop（uvicorn 等）— 委托到 search loop
            future = asyncio.run_coroutine_threadsafe(
                method(self, *args, **kwargs), search_loop
            )
            return await await_concurrent_future_guarded(future, search_loop)

    return wrapper


class _LoopThread:
    """
    后台线程持有一个持久的 event loop。

    解决问题：本地 Qdrant（AsyncQdrantLocal）通过文件锁绑定到创建它的 event loop，
    如果每次 asyncio.run() 创建新 loop，文件锁会冲突。
    用同一个 loop 运行所有协程即可。
    """

    def __init__(self):
        import threading
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._lag_lock = threading.Lock()
        self._lag_handle = None
        self._wakeup_guard_handle = None
        self._lag_current_ms = 0.0
        self._lag_max_ms = 0.0
        self._lag_samples = 0
        self._lag_over_100ms = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("background event loop failed to start")

    def _run(self):
        asyncio.set_event_loop(self._loop)
        if os.getenv("MEMORY_EVENT_LOOP_LAG_MONITOR_ENABLED", "false").lower() in (
            "1", "true", "yes", "on",
        ):
            self._schedule_lag_probe()
        # Some runtimes can lose the self-pipe wakeup used by
        # call_soon_threadsafe/run_coroutine_threadsafe.  Keep an internal timer
        # pending so the selector wakes at least every 50ms and processes any
        # cross-thread callbacks already queued.  This timer is scheduled by the
        # loop itself and therefore does not depend on the broken wakeup path.
        self._schedule_wakeup_guard()
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def _schedule_wakeup_guard(self):
        def _wake():
            self._schedule_wakeup_guard()

        self._wakeup_guard_handle = self._loop.call_later(0.05, _wake)

    def _schedule_lag_probe(self):
        try:
            interval = max(
                0.05,
                float(os.getenv("MEMORY_EVENT_LOOP_LAG_INTERVAL_SEC", "0.5")),
            )
        except (TypeError, ValueError):
            interval = 0.5
        expected = self._loop.time() + interval

        def _probe():
            now = self._loop.time()
            lag_ms = max(0.0, (now - expected) * 1000)
            with self._lag_lock:
                self._lag_current_ms = lag_ms
                self._lag_max_ms = max(self._lag_max_ms, lag_ms)
                self._lag_samples += 1
                if lag_ms >= 100.0:
                    self._lag_over_100ms += 1
            self._schedule_lag_probe()

        self._lag_handle = self._loop.call_at(expected, _probe)

    def lag_snapshot(self) -> Dict[str, Any]:
        with self._lag_lock:
            return {
                "current_ms": round(self._lag_current_ms, 3),
                "max_ms": round(self._lag_max_ms, 3),
                "samples": self._lag_samples,
                "over_100ms": self._lag_over_100ms,
            }

    def run(self, coro, *, include_runtime_diag: bool = False):
        """在后台 loop 中运行协程，同步等待结果"""
        if not include_runtime_diag:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return concurrent_result_guarded(future, self._loop)

        submitted_at = time.perf_counter()

        async def _timed():
            started_at = time.perf_counter()
            result = await coro
            if isinstance(result, dict):
                runtime_diag = result.setdefault("runtime", {})
                runtime_diag["submit_wait_ms"] = round(
                    (started_at - submitted_at) * 1000, 3,
                )
                runtime_diag["event_loop"] = self.lag_snapshot()
            return result

        future = asyncio.run_coroutine_threadsafe(_timed(), self._loop)
        return concurrent_result_guarded(future, self._loop)

    async def run_async(self, coro):
        """
        在后台 loop 中运行协程，异步等待结果。

        可从任意 event loop 中安全 await — 如果已经在正确的 loop 上则直接执行，
        否则委托到 _LoopThread 的 loop 并 await。
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is self._loop:
            # 已经在 _LoopThread 的 loop 里，直接执行
            return await coro
        else:
            # 在外部 loop 里（如 uvicorn），委托到 _LoopThread
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return await await_concurrent_future_guarded(future, self._loop)

    def stop(self):
        def _stop():
            if self._lag_handle is not None:
                self._lag_handle.cancel()
            if self._wakeup_guard_handle is not None:
                self._wakeup_guard_handle.cancel()
            self._loop.stop()

        self._loop.call_soon_threadsafe(_stop)
        self._thread.join(timeout=5)


class HyMemoryClient:
    """
    HY Memory 无状态客户端。

    构造函数只接受连接配置和处理模式，不绑定任何业务参数。
    所有业务隔离参数（user_id/agent_id/session_id 等）在每次调用时传入，
    一个 client 实例可被多线程安全共享。

    处理模式（mode）：
    - "lite":  raw-only 纯 embedding 写入/检索，不调用 LLM；忽略 scene/layers 路由
    - "pro":   embedding + LLM agent 提取（事实/实体/意图），标准模式
    - "ultra": pro + 可显式触发的 System 2 认知加工（Schema 归纳），完整模式

    数据隔离模型（三级）：
    - user_id:    一级 key — 每个用户唯一的记忆库
    - agent_id:   二级 key — 同一用户下不同 Agent 场景的隔离
    - session_id: 三级 key — 同一 Agent 下不同会话的隔离

    Usage:
        # lite: 纯向量写入，不调 LLM
        client = HyMemoryClient(mode="lite")

        # pro: 标准模式（embedding + agent 提取）
        client = HyMemoryClient(mode="pro")

        # ultra: 完整模式（pro + System 2 认知层）
        client = HyMemoryClient(mode="ultra")

        client.add("用户喜欢科幻电影", user_id="user_001")
        results = client.search("用户喜欢什么？", user_ids=["user_001"])
        client.close()

    Args:
        config: 自定义 MemoryConfig（默认从环境变量加载）
        mode:   处理模式 ("lite" / "pro" / "ultra")，默认从环境变量 MEMORY_MODE 读取
    """

    # 合法的 mode 值
    VALID_MODES = ("lite", "pro", "ultra")

    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        mode: Optional[str] = None,
        coding_writer: Optional[str] = None,
        runtime: Optional["SharedRuntime"] = None,
    ):
        # 确保日志系统已配置（幂等，多次调用安全）
        setup_logging()

        # 配置
        self._config = config or MemoryConfig.from_env()

        # coding_writer 显式传入则覆盖 config / env（默认 None → 沿用 config.coding.writer）
        # 取值: "legacy" | "agent"
        if coding_writer is not None:
            cw = str(coding_writer).strip().lower()
            if cw in ("legacy", "agent"):
                self._config.coding.writer = cw
            else:
                logger.warning(
                    f"[init] invalid coding_writer={coding_writer!r}; falling back to config default"
                )

        # 解析 mode
        import os
        self._mode = mode or os.getenv("MEMORY_MODE", "pro")
        if self._mode not in self.VALID_MODES:
            raise ValueError(
                f"Invalid mode '{self._mode}'. Must be one of: {self.VALID_MODES}"
            )

        # 图数据库开关：与 mode 解耦。
        #   ultra           → 恒开（System 2 写 L6_SCHEMA 依赖它）
        #   pro             → 由 MEMORY_ENABLE_GRAPH 决定（默认关），用于记忆关系图
        #   lite            → 恒关（lite 不做任何 LLM 加工，图无从建立）
        # GraphStore 的初始化本来就在 client 启动时完成、与 evolve()/System 2 无关，
        # 所以 pro 开图不需要把 System 2 拖进来。
        if self._mode == "ultra":
            self.enable_graph = True
        elif self._mode == "pro":
            from .config import _get_env_bool
            self.enable_graph = _get_env_bool("MEMORY_ENABLE_GRAPH", False)
        else:
            self.enable_graph = False
        self._config.enable_graph = self.enable_graph

        # 进程级初始化日志强制不带业务 request_id（哨兵 "-"），避免污染 App
        # 请求上下文里按 request_id 的过滤。
        with request_id_scope():
            logger.info(
                f"Initializing HyMemoryClient: mode={self._mode} "
                f"enable_graph={self.enable_graph}"
            )

            # SharedRuntime 接入（多 client 部署用，避免 cross-loop bug）
            # - runtime is None  → solo 模式：自己起 _LoopThread + 自己的 cache pool
            # - runtime is given → shared 模式：复用 runtime 的 loop / cache / metrics 绑定
            self._runtime = runtime
            self._owns_runtime = runtime is None

            # 创建/复用持久 event loop
            if self._owns_runtime:
                self._loop_thread = _LoopThread()
                # solo 模式：search 也开一个独立 loop，隔离效果与 shared 模式一致。
                self._search_loop_thread = _LoopThread()
            else:
                self._loop_thread = runtime.loop_thread
                # shared 模式：用 runtime 的 search 专用 loop（与 write loop 隔离）。
                # 兼容旧 runtime（无 search_loop_thread）时回落到 write loop。
                self._search_loop_thread = (
                    getattr(runtime, "search_loop_thread", None) or runtime.loop_thread
                )

            # 同步初始化所有组件
            self._loop_thread.run(self._initialize())

            logger.info("HyMemoryClient ready")

    async def _initialize(self):
        """初始化所有核心组件"""
        # 进程级初始化日志不应继承外层（如 App 请求上下文）的业务 request_id，
        # 否则按 request_id 过滤会混入一次性 init 噪音。强制设为哨兵 "-"。
        with request_id_scope():
            await self._initialize_impl()

    async def _initialize_impl(self):
        """初始化所有核心组件（实际实现）"""

        # 0. 扩大全局默认线程池 + 各组件后续用独立池
        import concurrent.futures
        loop = asyncio.get_event_loop()
        _max_workers = int(os.getenv("MEMORY_THREAD_POOL_SIZE", "256"))
        loop.set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=_max_workers)
        )
        logger.info(f"[init] Default ThreadPoolExecutor max_workers={_max_workers}")

        # 1. EmbedService（纯配置，首次调用时真正连接 API）
        self._embed_service = EmbedService(self._config)
        logger.debug(
            f"[init] EmbedService created: model={self._config.embedder.model}"
        )

        # 2. VectorStore（根据配置选择后端）
        self._vector_store = create_vector_store(self._config)
        await self._vector_store.initialize()
        logger.info(
            f"[init] VectorStore ready: provider={self._config.vector_store.provider} "
            f"collection={self._vector_store._collection_name}"
        )

        # 3. GraphStore（可选，由 enable_graph 配置控制）
        self._graph_store = None
        if self.enable_graph:
            from .data.graph_store import create_graph_store
            self._graph_store = create_graph_store(self._config)
            await self._graph_store.initialize()
            # 检查图数据库是否真正可用（Kuzu 会静默降级为 no-op）
            if hasattr(self._graph_store, '_available') and not self._graph_store._available:
                raise RuntimeError(
                    f"GraphStore ({self._config.graph_store.provider}) init failed. "
                    f"Use mode='pro' instead of mode='ultra' to disable Graph, or check logs for details."
                )
            logger.info(
                f"[init] GraphStore ready: provider={self._config.graph_store.provider}"
            )

        # 4. ComponentFactory（注入已初始化的共享组件）
        self._registry = ComponentFactory(config=self._config)
        self._registry._shared_embed_service = self._embed_service
        self._registry._shared_vector_store = self._vector_store
        self._registry._shared_vector_store_initialized = True
        if self._graph_store is not None:
            self._registry._shared_graph_store = self._graph_store

        # 5. Cache（必须在组件预热前初始化，writer 创建时需要注入 cache）
        # - solo 模式：自己 create + initialize
        # - shared 模式：复用 runtime 的 cache（已 init + 已装 observability hook）
        if self._owns_runtime:
            self._cache = create_cache(self._config)
            await self._cache.initialize()
            logger.info(f"[init] Cache ready (backend={self._config.cache.backend})")
        else:
            self._cache = self._runtime.cache
            logger.info("[init] Cache reused from SharedRuntime")

        self._registry._shared_cache = self._cache

        # 5.1 Pipeline 可观测性 hook
        # - solo 模式：自己装一次
        # - shared 模式：runtime 已经在 shared cache 上装过了，跳过避免重复包装
        if self._owns_runtime:
            self._install_pipeline_observability_hooks()

        # 6. 预热组件（确保首次 add/search 不需要等待初始化）
        await self._registry.get_writer()
        await self._registry.get_reader()
        logger.info(f"[init] Writer & Reader ready (mode={self._mode})")

        await self._initialize_local_reranker()

        if self._mode == "ultra":
            try:
                await self._registry.get_system2_writer()
                logger.info("[init] System2Writer ready (ultra mode)")
            except Exception as e:
                logger.warning(f"[init] System2Writer warmup failed: {e}")

        # 7. HistoryStore（可选，由 config.history.enable 控制）
        self._history_store = None
        if self._config.history.enable:
            self._history_store = HistoryStore(self._config)
            await self._history_store.initialize()
            logger.info("[init] HistoryStore ready")

        # 8. MetricsCollector
        # - solo 模式：bind cache 到 metrics 单例 + 启 background flush
        # - shared 模式：runtime 已经做过，跳过（关键！避免覆盖 _cache 触发 cross-loop）
        if self._owns_runtime:
            from .metrics import MetricsCollector
            _metrics = MetricsCollector.get()
            _metrics.bind_cache(self._cache)
            await _metrics.start_background_tasks()
            logger.info("[init] MetricsCollector ready")
        else:
            logger.info("[init] MetricsCollector already bound by SharedRuntime")

        # 9. Coding memory 链路占位（lazy 初始化）
        #    仅在 add(messages_with_tool) 首次命中 is_coding=True 时才真正初始化。
        self._coding_writer = None       # CodingWriter | None
        self._coding_store = None        # CodingMemoryStore | None
        self._coding_llm_provider = None  # LLMProvider | None
        self._coding_init_lock = asyncio.Lock()
        # ── 按变体缓存 ────────────────────────────────────────────────────
        # 两个变体的库是**分开**的（见 CodingMemoryStore.__init__ 的 _suffix 注释），
        # 所以不能只留一份 store —— 否则 add(scene="working") 之后
        # search(scene="coding") 会去读 working 的库，静默读错。
        # `_coding_store` / `_coding_writer` 保留为"最近一次用的那个"，读取侧通过
        # _store_for_variant() 取正确的那一份。
        self._coding_writers: Dict[str, Any] = {}   # variant → CodingWriter
        self._coding_stores: Dict[str, Any] = {}    # variant → CodingMemoryStore
        # 版本演进链层（scene="coding" 独有）。零 LLM 纯 sqlite，见 _get_chain_store。
        self._chain_store = None         # HyCodeMem | None
        self._chain_store_failed = False  # 初始化失败过就不再重试
        # 链层反查真实 diff 用的本地仓路径。None = 只用对话里的 old/new 兜底抽取；
        # 给不了本地仓也能用，只是 diff 来自对话原文而非 git。
        self._chain_repo_root = os.getenv("MEMORY_CODING_CHAIN_REPO_ROOT") or None

    async def _initialize_local_reranker(self) -> None:
        """Load and warm the configured reranker before the client is ready."""
        if not getattr(self._config.recall, "local_rerank_enabled", False):
            return

        from .pipelines._retrieval.local_rerank import get_local_reranker

        reranker = get_local_reranker(
            getattr(
                self._config.recall,
                "local_rerank_model",
                "nreimers/mmarco-mMiniLMv2-L6-H384-v1",
            ),
            device=getattr(self._config.recall, "local_rerank_device", "auto"),
            max_length=getattr(
                self._config.recall, "local_rerank_max_length", 512,
            ),
            batch_size=getattr(
                self._config.recall, "local_rerank_batch_size", 16,
            ),
        )
        try:
            from .utils.async_executor import to_thread_guarded

            rerank_init = await to_thread_guarded(reranker.initialize)
        except Exception as exc:
            raise RuntimeError(
                "Local rerank is enabled but initialization failed: "
                f"model={reranker.model_name!r}, error={exc}"
            ) from exc
        logger.info(
            "[init] Local reranker ready: model=%s device=%s "
            "load_ms=%s warmup_ms=%s",
            reranker.model_name,
            reranker.device,
            rerank_init.get("load_ms", 0),
            rerank_init.get("warmup_ms", 0),
        )

    def _install_pipeline_observability_hooks(self) -> None:
        """
        包装 cache.store_pipeline_log：
        - Log：JSONL 文件，始终写入（环节级，不依赖 Trace 开关）
        - Trace：SQLite pipeline_logs，由 MEMORY_PIPELINE_TRACE_ENABLED 控制（Inspector）
        - 落盘 fire-and-forget，不阻塞 search/write 热路径
        """
        from .utils.pipeline_observability import install_pipeline_observability

        install_pipeline_observability(self._cache, log_label="init")

    @classmethod
    def from_config(
        cls,
        config_dict: Dict[str, Any],
        mode: Optional[str] = None,
    ) -> "HyMemoryClient":
        """
        从字典创建客户端（类似 mem0 风格）。

        Usage:
            client = HyMemoryClient.from_config({
                "vector_store": {
                    "provider": "qdrant",
                    "collection_name": "my_memories",
                },
                "graph_store": {
                    "provider": "neo4j",
                    "url": "bolt://localhost:7687",
                    "username": "neo4j",
                    "password": "password",
                },
                "enable_graph": True,
                "llm": {
                    "provider": "openai",
                    "model": "deepseek-chat",
                    "api_key": "sk-xxx",
                },
                "embedder": {
                    "provider": "openai",
                    "model": "text-embedding-v3",
                    "api_key": "sk-xxx",
                },
            })
        """
        config = MemoryConfig.from_dict(config_dict)
        resolved_mode = mode or config_dict.get("mode")
        return cls(config=config, mode=resolved_mode)

    # ================================================================
    # 属性
    # ================================================================

    @property
    def mode(self) -> str:
        """当前处理模式 (lite/pro/ultra)"""
        return self._mode

    # ================================================================
    # 内部
    # ================================================================

    @staticmethod
    def _build_isolation_key(uid: str, agent_id: str) -> str:
        """
        构建 history 用的 isolation_key。

        两级隔离: "{user_id}:{agent_id}"
        """
        parts = [uid or "default", agent_id or "default"]
        return ":".join(parts)

    @staticmethod
    def _normalize_scene(scene: Optional[str]) -> Optional[str]:
        """
        归一化用户显式指定的场景参数 —— 只解析**路由**，不区分变体。

        只接受三个值（大小写不敏感）：
          - "chat"    → 走 chat 链路
          - "working" → 走 coding 链路（通用生产力变体）
          - "coding"  → 走 coding 链路（软件工程变体，见 _scene_variant）
          - None      → 未指定，由 LLM 自动判定

        "working" 和 "coding" 在这里归一成同一个返回值 "coding"：它们走的是同一种
        生产力 pipeline，但使用按变体隔离的 store / writer / reader。抽取规范及是否挂
        版本链层由 _scene_variant() 单独解析。这样 add()/search() 里所有
        `scene_forced == "coding"` 的判断对两者同时生效，不必逐处加分支。

        Returns:
            "coding" | "chat" | None
        非法值抛 ValueError。
        """
        if scene is None:
            return None
        s = str(scene).strip().lower()
        if s == "chat":
            return "chat"
        if s in ("working", "coding"):
            return "coding"
        raise ValueError(
            f"scene must be 'chat', 'working' or 'coding', got {scene!r}"
        )

    @staticmethod
    def _scene_variant(scene: Optional[str]) -> str:
        """
        解析 coding 链路的**变体**。与 _normalize_scene 正交：那个决定走哪条链，
        这个决定走上去之后用哪套规范。

          - "coding"        → "coding"   软件工程场景
          - 其他 / None     → "working"  通用生产力场景（原有行为）

        这个 helper 对 None 返回 working 只是兼容 fallback；真正的 scene=None 自动
        路由会由三分类 judge 回填 working/coding，不会固定吞成 working。

        两个变体共用表结构和处理流程，但使用独立 SQLite 文件与向量集合。行为差异为：
          1. 抽取规范 —— coding 用 EXTRACT_PROMPT_CODING（HARD CONSTRAINT 头部 +
             CODE WORK 节：分支名逐字、哪一版赢了、精确坐标、verified vs written）。
          2. 版本演进链层 —— coding 额外挂 coding.chain_store：零 LLM 地存下每处
             代码每一版的真实 patch。治的是抽取规范本身砍掉的那类信息（它明文要求
             "EXACT COORDINATES, NOT PASTED CODE"，而被撤掉那版的代码不在工作区、
             不在 HEAD，只在某次中间提交里，读不回来）。
        """
        return "coding" if str(scene or "").strip().lower() == "coding" else "working"

    @staticmethod
    def _parse_input(data: AddInput):
        """
        解析 add() 的输入，统一返回 (content, chat_messages, content_type)。

        str  → ("文本", [], "text")
        list → ("", [ChatMessage, ...], "messages")

        list 模式下兼容两种 tool 调用风格，规范化到统一的 ChatMessage：
        - OpenAI: assistant.tool_calls (function.arguments 是 JSON 字符串) +
                   独立 role="tool" 消息（含 tool_call_id / name）
        - Anthropic: assistant.content 为 list[content_block]，含 type=tool_use；
                     tool_result 嵌在后续 role="user" 消息的 content list 里，
                     需要拆为独立的 role="tool" 规范化消息

        一条原始消息可能产出 0~多 条 ChatMessage（Anthropic tool_result 拆分）。
        """
        if isinstance(data, str):
            return data, [], "text"

        if isinstance(data, list):
            chat_messages: List[ChatMessage] = []
            for raw in data:
                chat_messages.extend(HyMemoryClient._normalize_message(raw))
            return "", chat_messages, "messages"

        raise TypeError(
            f"add() 第一个参数应为 str 或 list[dict]，收到 {type(data).__name__}"
        )

    @staticmethod
    def _normalize_message(m: Dict[str, Any]) -> List[ChatMessage]:
        """
        把单条原始消息规范化到 ChatMessage 列表。

        返回值通常是单条；当原始消息是 Anthropic 风格 user 消息且其 content
        list 含 tool_result block 时，会拆为 [user_text_msg?, tool_msg, ...]。
        """
        role = m.get("role", "user")
        raw_content = m.get("content")
        raw_tool_calls = m.get("tool_calls", []) or []

        # 部分客户端（如 codebuddy）会把 tool_calls 编码成 JSON 字符串而非 list，
        # 这里统一规范化为 list，避免下面的 for 循环把字符串当成可迭代对象。
        if isinstance(raw_tool_calls, str):
            try:
                parsed_tool_calls = json.loads(raw_tool_calls) if raw_tool_calls else []
            except Exception:
                logger.warning(
                    "tool_calls 是非法 JSON 字符串，已忽略: %r", raw_tool_calls[:200]
                )
                parsed_tool_calls = []
            raw_tool_calls = parsed_tool_calls if isinstance(parsed_tool_calls, list) else []
        elif not isinstance(raw_tool_calls, list):
            raw_tool_calls = []

        # ── 1. OpenAI assistant.tool_calls ──
        tool_calls: List[ToolCall] = []
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                # OpenAI 编码为 JSON 字符串
                try:
                    args = json.loads(args) if args else {}
                except Exception:
                    args = {"_raw": args}
            elif args is None:
                args = {}
            elif not isinstance(args, dict):
                args = {"_raw": args}
            tool_calls.append(ToolCall(
                id=tc.get("id", "") or "",
                name=fn.get("name", "") or "",
                arguments=args,
            ))

        # ── 2. OpenAI 独立 tool 消息 ──
        # 注意：tool 消息的 `name` 是**工具名**，不是说话人名，仍归 tool_name。
        if role == "tool":
            return [ChatMessage(
                role="tool",
                content=str(raw_content or ""),
                tool_call_id=m.get("tool_call_id"),
                tool_name=m.get("name"),
            )]

        # 非 tool 角色的 `name` 是**说话人显示名**（OpenAI messages 规范）。
        # 多真人对话靠它区分对方是谁：role=assistant + name → 对方真人而非 AI。
        speaker_name = m.get("name") or None

        # ── 3. Anthropic content blocks ──
        if isinstance(raw_content, list):
            text_parts: List[str] = []
            tool_result_msgs: List[ChatMessage] = []
            for block in raw_content:
                if not isinstance(block, dict):
                    # 非标准形态：当字符串处理
                    text_parts.append(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", "") or "")
                elif btype == "tool_use":
                    # Anthropic：input 已是 dict
                    tool_input = block.get("input", {}) or {}
                    if not isinstance(tool_input, dict):
                        tool_input = {"_raw": tool_input}
                    tool_calls.append(ToolCall(
                        id=block.get("id", "") or "",
                        name=block.get("name", "") or "",
                        arguments=tool_input,
                    ))
                elif btype == "tool_result":
                    # 拆为独立 role=tool 消息
                    tc_id = block.get("tool_use_id")
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        # 嵌套 content list（通常是 [{type:text,text:...}]）
                        inner = "\n".join(
                            b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                            else str(b)
                            for b in inner
                        )
                    elif not isinstance(inner, str):
                        inner = str(inner)
                    tool_result_msgs.append(ChatMessage(
                        role="tool",
                        content=inner,
                        tool_call_id=tc_id,
                        tool_name=None,  # Anthropic tool_result 没带 name
                    ))
                # 其他未知 block 类型静默忽略
            text = "\n".join(p for p in text_parts if p).strip()

            outputs: List[ChatMessage] = []
            # 即使 text 为空，只要 tool_calls 非空也保留 assistant 消息
            if text or tool_calls:
                outputs.append(ChatMessage(
                    role=role,
                    content=text,
                    name=speaker_name,
                    tool_calls=tool_calls,
                ))
            outputs.extend(tool_result_msgs)
            return outputs

        # ── 4. 普通 string content ──
        # role=="tool" 已在分支 2 提前返回，这里不会是 tool 消息，故不再填 tool_name
        # （旧代码把 `name` 塞进 tool_name，对非 tool 角色是无意义的）。
        content_str = "" if raw_content is None else str(raw_content)
        return [ChatMessage(
            role=role,
            content=content_str,
            name=speaker_name,
            tool_calls=tool_calls,
            tool_call_id=m.get("tool_call_id"),
        )]

    # ================================================================
    # Coding memory lazy init
    # ================================================================

    async def _get_coding_writer(self, variant: str = "working"):
        """
        Lazy 初始化 coding 写入链路。**按变体各一份**。

        命中 is_coding=True 时才真正构造 store / extractor / reconciler。
        chat 路径的用户永远不会触发这块开销。

        legacy CodingWriter = extractor + reconciler。

        variant("working" | "coding") 决定两件事：
          1. 用哪份抽取规范（见 _scene_variant）
          2. 读写哪个库 —— 两个变体的 sqlite 和向量集合都是分开的

        两个变体各缓存一份 writer/store，可以在同一个 client 上混用：
        add(scene="working") 和 add(scene="coding") 各写各的库，
        search 时按 scene 取对应那一份，不会串。

        `self._coding_store` / `self._coding_writer` 同步指向**最近一次**用的那份，
        只为兼容既有代码路径（如 list_memories）。要按变体取请走
        _store_for_variant()。
        """
        cached = self._coding_writers.get(variant)
        if cached is not None:
            self._coding_writer = cached
            self._coding_store = self._coding_stores.get(variant)
            return cached
        async with self._coding_init_lock:
            cached = self._coding_writers.get(variant)
            if cached is not None:
                self._coding_writer = cached
                self._coding_store = self._coding_stores.get(variant)
                return cached

            # 局部 import 避免冷启动开销
            from .agent.llm_provider import LLMProvider
            from .coding.store import CodingMemoryStore

            # 共享 LLMProvider（与 chat 链路相同 LLMConfig.model）
            if self._coding_llm_provider is None:
                self._coding_llm_provider = LLMProvider(self._config)

            # Store —— 按变体分库（db_path 和两个向量集合都带后缀），见
            # CodingMemoryStore.__init__ 里 _suffix 的注释。variant="working" 时
            # 后缀为空，路径与集合名和改动前逐字相同。
            store = CodingMemoryStore(
                self._config,
                embed_service=self._embed_service,
                db_path=self._config.coding.db_path,
                variant=variant,
            )
            await store.initialize()

            # extractor + reconciler 串联
            from .coding.extractor import CodingMemoryExtractor
            from .coding.reconciler import CodingMemoryReconciler
            from .coding.writer import CodingWriter

            extractor = CodingMemoryExtractor(
                self._coding_llm_provider,
                cache=self._cache,
                scene=variant,
                verbatim_fallback_enabled=bool(
                    getattr(
                        self._config.coding,
                        "verbatim_fallback_enabled",
                        False,
                    )
                ),
            )
            reconciler = CodingMemoryReconciler(
                store=store,
                embed_service=self._embed_service,
                llm_provider=self._coding_llm_provider,
                cache=self._cache,
            )

            writer = CodingWriter(
                store=store,
                extractor=extractor,
                reconciler=reconciler,
                llm_provider=self._coding_llm_provider,
                embed_service=self._embed_service,
                cache=self._cache,
            )
            self._coding_stores[variant] = store
            self._coding_writers[variant] = writer
            # 兼容既有路径：指向最近一次用的那份
            self._coding_store = store
            self._coding_writer = writer
            logger.info(
                "[coding] writer chain initialized (lazy), variant=%s", variant
            )
            return writer

    def _store_for_variant(self, variant: str = "working"):
        """取某个变体的 store。没初始化过返回 None。

        读取侧必须走这个而不是直接读 self._coding_store —— 后者是"最近一次用的
        那份"，在 add(working) 之后 search(coding) 会拿到 working 的库。
        """
        return self._coding_stores.get(variant) or None

    @staticmethod
    def _chain_owner(user_id=None, workspace_id=None) -> str:
        """链层的隔离键。

        `<user_id>|<workspace_id>` —— 两个都空时返回 ""。空串只匹配空串，
        不是公共池，所以旧数据不会被新写入的行串进来。

        为什么 user_id 必须在键里：thread 的键原来只有 (branch, file, pkey)，
        不带 owner 时不同用户的同名 branch/file/pkey 会并成一条链，造成数据泄漏。
        """
        u = str(user_id or "").strip()
        w = str(workspace_id or "").strip()
        return "" if not (u or w) else "%s|%s" % (u, w)

    def _get_chain_store(self, owner: str = ""):
        """
        Lazy 初始化版本演进链层（scene="coding" 独有）。

        零 LLM、零 embedding、纯 sqlite —— 构造代价可以忽略，所以不用异步锁。
        失败返回 None：这层是**增量**，拿不到就退回纯向量双层（= working 的行为），
        不该让一次 add/search 整体失败。

        owner 变了要换实例（同一个 client 服务多个 user 时）—— 实例只是个 sqlite
        连接 + owner 字符串，重建很便宜，而复用会让 owner 过滤失效。
        库文件是同一个，所以不同 owner 共享文件、靠 WHERE 隔离。

        库位置：跟 coding_memory.db 同目录下的 hy_code_chain/hy_code.db。不并进同一个
        文件是因为 CodingMemoryStore 自己管 schema 迁移，塞三张表进去会让两边的迁移
        逻辑互相踩；两个文件同目录，备份/清理时不会漏。
        """
        if self._chain_store is not None and getattr(
                self._chain_store, "owner", "") == owner:
            return self._chain_store
        if self._chain_store_failed:
            return None
        try:
            import os as _os
            from .coding.chain_store import HyCodeMem
            _dir = _os.path.join(
                _os.path.dirname(_os.path.abspath(self._config.coding.db_path)),
                "hy_code_chain",
            )
            self._chain_store = HyCodeMem(
                _dir, repo_root=self._chain_repo_root, owner=owner)
            logger.info("[coding] chain store initialized at %s (owner=%r)",
                        _dir, owner)
            return self._chain_store
        except Exception as e:
            # 只试一次。失败通常是环境性的（目录不可写 / sqlite 不可用），重试每次
            # add 都付一遍代价却不会变好。
            self._chain_store_failed = True
            logger.warning(
                "[coding] chain store init failed, falling back to vector-only: %s", e
            )
            return None

    # ----------------------------------------------------------------
    # 版本演进链层的两个胶水方法（写 / 读）
    # ----------------------------------------------------------------

    @staticmethod
    def _chain_text(chat_messages) -> str:
        """把一次 add 的 messages 渲染成链层能读的纯文本。

        形态刻意对齐 chain_store 里 _USER_LC / _DIFF_END 认的小写前缀
        （`user:` / `assistant:` / `[tool:name]`）。大写 `USER:` 也能被 _USER 识别，
        但工具消息无法进入同一套边界判定。

        为什么不复用 CodingMemoryExtractor._format_conversation：那个给每行加了
        `[i] ` 下标前缀（给 LLM 读的），链层的正则锚在行首，前缀一加全都匹配不上。
        """
        rows = []
        for m in (chat_messages or []):
            try:
                content = m.content if getattr(m, "content", None) else ""
                if getattr(m, "is_tool_message", None) and m.is_tool_message():
                    rows.append(f"[tool:{getattr(m, 'tool_name', None) or '?'}] {content}")
                    continue
                role = getattr(m, "role", "") or "user"
                rows.append(f"{role}: {content}")
            except Exception:
                continue
        return "\n".join(rows)

    def _observe_chain(self, chat_messages, *, user_id=None, workspace_id=None,
                       branch=None) -> int:
        """写入侧：把这一段对话里的代码改动存进版本演进链。

        返回存下的 hunk 数（0 = 这段没有可识别的代码改动，正常情况，不是错误）。

        全程零 LLM、零 embedding、纯 sqlite + 正则，适合在写链路同步执行。

        best-effort：任何异常只记 warning。这层是增量，它失败时向量双层已经写完了，
        把整个 add 拖失败是净亏。
        """
        # 走类而不是 self：_chain_owner 是 staticmethod，而这两个方法会被测试绑到裸
        # SimpleNamespace 上调（同 _scene_variant 那处），self 上没有这个属性。
        store = self._get_chain_store(
            HyMemoryClient._chain_owner(user_id, workspace_id))
        if store is None:
            return 0
        try:
            text = self._chain_text(chat_messages)
            if not text.strip():
                return 0
            # branch 给了就不猜（meta 里的 branch 优先于从文本里正则认）。
            # workspace_id 不单独透传给 observe：它已经进了 owner（见 _chain_owner），
            # thread 的键里不再需要第二个仓维度。
            return int(store.observe(text, meta={"branch": branch or ""}) or 0)
        except Exception as e:
            logger.warning("[coding] chain observe failed: %s", e)
            return 0

    def _decorate_with_chain(self, facts, instruction: str = "", scope=None,
                             user_id=None, workspace_id=None):
        """读取侧：给向量层召回的 facts 挂上版本演进链块。

        返回 (block, used_idx)：
          block    —— 要放在 fact 列表**之前**的多行结构块（挂不上任何链时为 ""）
          used_idx —— 已挂进 block 的 fact 下标；调用方应把这些从 fact 列表里去掉，
                      避免同一条 fact 出现两次。

        facts 顺序不重排 —— top-k 是向量层的事，这层只负责"每条 fact 后面跟什么"。

        best-effort：失败返回 ("", [])，退化成纯向量双层（= working 的行为）。
        """
        if not facts:
            return "", []
        # 走类而不是 self：_chain_owner 是 staticmethod，而这两个方法会被测试绑到裸
        # SimpleNamespace 上调（同 _scene_variant 那处），self 上没有这个属性。
        store = self._get_chain_store(
            HyMemoryClient._chain_owner(user_id, workspace_id))
        if store is None:
            return "", []
        try:
            return store.decorate(facts, instruction=instruction, scope=scope)
        except Exception as e:
            logger.warning("[coding] chain decorate failed: %s", e)
            return "", []

    async def _search_coding(
        self,
        target_query: str,
        *,
        user_ids: List[str],
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        workspace_id: Optional[str],
        branch: Optional[str],
        limit: int,
        working_fact_min_score: Optional[float] = None,
        working_experience_min_score: Optional[float] = None,
        request_id: str,
        t0: float,
        variant: str = "working",
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Coding 召回路径（事实主导的双层召回）：
          1. 拿 store（lazy init），ensure 已 initialize
          2. embed(target_query) —— 一次，两层共用同一个向量
          3. 事实主索引：召回 top-K 的 active facts
          4. 顺链带出经验：沿命中事实的 derived_experience_ids 拉经验
          5. 经验直查兜底：同一向量直接搜经验层，合并进来
          6. 去重（多事实指向同一经验只留一条）+ 存在性过滤（死链自然滤掉）
          7. 标注 via: fact_link / direct 后返回

        limit 是**每层的返回上限**：facts ≤ limit，experiences ≤ limit，所以一次调用
        最多给出 2*limit 条。两层分开计数而不是共用一个总预算——它们承担的角色不同，
        事实答"是什么"、经验答"怎么做"，谁挤掉谁都不合理。

        取数时都会多捞一些再落回 limit：事实层按 max(limit, 5) 取、经验层顺链和直查
        各取至多 limit 条，中间要过阈值、去重、剔死链，不多捞就填不满。

        两道闸门，每层一道，谁也不豁免：
          working_fact_min_score        事实层，默认 0.30
          working_experience_min_score  经验层，默认事实阈值 + 0.10

        经验那道对顺链和直查一视同仁。两路卡的本来就是同一批分数（都来自下面那次
        直查，顺链只是借用），同一个尺度上再分两档没有依据。事实背书体现在**排序**
        上——顺链的排在直查前面，limit 截断时先保住——而不是准入上打折。

        workspace_id / branch 是**作用域筛选条件**，两层都生效：不传就不筛（全量
        召回），传了则只在作用域内召回。规则由 coding.store._scope_where 定义，与
        list_memories 共用一套：workspace_id 精确匹配；branch 只在 workspace_id
        非空时叠加，且容 NULL（没记录分支的行不算"记录了别的分支"）。过滤下推到
        SQLite（这两列的权威来源），向量层按作用域选择率放大取数，所以过滤不吃召回。

        作用域把事实层筛空时，顺链那一路跟着断源，本次检索退化成只有经验直查的单
        层。这是可接受的——事实是"这个 repo 里发生过什么"，跨 workspace 拿它去带
        经验没有意义——但结果里看不出来，所以退化时打 warning。

        失败时 fail-safe：返回 success=True + 两层都空（不抛错）。
        """
        fact_min_score = (_WORKING_FACT_DEFAULT_MIN_SCORE
                          if working_fact_min_score is None else working_fact_min_score)
        exp_min_score = (fact_min_score + _WORKING_EXPERIENCE_MIN_SCORE_DELTA
                         if working_experience_min_score is None
                         else working_experience_min_score)
        # coding 路径 trace（与 chat 路径共用 pipeline_logs；best-effort）
        _tracer = None
        if getattr(self, "_cache", None) is not None:
            try:
                from .pipelines._retrieval.trace import ReadTraceLogger
                _tracer = ReadTraceLogger(
                    cache=self._cache,
                    request_id=request_id,
                    user_id=(user_ids[0] if user_ids else ""),
                    agent_id="default_agent",
                    reader_version="coding",
                )
            except Exception:
                _tracer = None

        def _empty() -> Dict[str, Any]:
            _r = {
                "request_id": request_id,
                "scene": "productivity",
                "facts": [],
                "experiences": [],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }
            # 键集合与成功路径保持一致 —— 否则调用方对 fail-safe 的空结果要写两套
            # 取值逻辑，而 fail-safe 本来就是"不抛错，返回空"。
            if variant == "coding":
                _r["chain_block"] = ""
                _r["chain_used"] = []
            return _r

        try:
            # ★ 必须传 variant：两个变体的库是分开的，不传就会去读 working 的库
            #   （静默读错，症状是"明明灌进去了却召不回来"）。
            #   现在按变体各缓存一份 writer/store，所以传了也不会把另一个变体的
            #   extractor 定死 —— 早先那版单实例缓存才有那个问题。
            #
            # 用 TypeError 兜一次零参调用：测试会把这个方法换成零参 stub
            # （tests/coding/ 里三处 _stub_get_coding_writer），而它们只是要
            # "store 已就绪"这个副作用，不关心变体。给 stub 硬塞 kwarg 会让
            # 一批与本改动无关的测试红掉，那是接口洁癖而非正确性。
            try:
                await self._get_coding_writer(variant=variant)  # 副作用：初始化 store
            except TypeError as _te:
                if "variant" not in str(_te):
                    raise
                await self._get_coding_writer()
        except Exception as e:
            logger.warning(f"[search-coding] init failed: {e}")
            return _empty()

        # 取**这个变体**的 store。不直接用 self._coding_store —— 那是"最近一次用的
        # 那份"，add(working) 之后 search(coding) 会拿到错的库。
        #
        # 两级兜底都是为测试留的，不是防御性编程：tests/coding/ 的 fixture 把这个方法
        # 绑到裸 SimpleNamespace 上调（self 上既没有 _coding_stores 也没有
        # _store_for_variant），而它们直接往 ns 上挂好了 _coding_store。
        _store = (getattr(self, "_coding_stores", None) or {}).get(variant) \
            or getattr(self, "_coding_store", None)

        # 多 user_ids 时取首位（coding memory 一般跟单 user 绑定；
        # 跨 user 的 coding memory 检索属未来扩展）
        primary_user = user_ids[0]

        try:
            _t_embed = time.perf_counter()
            embedding, embed_meta = await self._embed_service.embed_with_meta(target_query)
            if _tracer is not None:
                await _tracer.log_coding_embed(
                    query=target_query, dims=len(embedding or []),
                    elapsed_ms=(time.perf_counter() - _t_embed) * 1000,
                    provider_meta=embed_meta,
                )
        except Exception as e:
            logger.warning(f"[search-coding] embed failed: {e}")
            return _empty()

        # ---- 1. 事实主索引 ----
        try:
            fact_hits = await _store.search_facts(
                embedding, user_id=primary_user,
                agent_ids=agent_ids, session_ids=session_ids,
                workspace_id=workspace_id, branch=branch,
                top=max(limit, 5),
                # 词面通道 + 把下面那道闸门下移到重排之前：先砍掉过不了闸的候选再重排，
                # 返回条数就恒等于纯稠密，词面只改选谁（理由见 store.search_facts）。
                query_text=target_query,
                min_score=fact_min_score,
                include_sources=include_sources,
                exclude_sources=exclude_sources,
            )
        except Exception as e:
            logger.warning(f"[search-coding] fact search failed: {e}")
            fact_hits = []
        # 事实层是主索引，噪声会顺 derived_experience_ids 放大成一串无关经验，
        # 所以这道闸门比经验直查那路更要紧
        _fact_top_score = max((s for _, s in fact_hits), default=0.0)
        _fact_raw = len(fact_hits)
        fact_hits = [(f, s) for f, s in fact_hits if s >= fact_min_score]
        if _fact_raw and not fact_hits:
            # 召回到了事实但全被阈值挡下：顺链那一路跟着断源，本次检索实际退化成
            # 只有经验直查的单层。这种退化在结果里看不出来（经验照样有），只能在
            # 这里报，否则阈值调过头了没人知道。
            logger.warning(
                f"[search-coding] fact layer emptied by threshold: "
                f"{_fact_raw} hit(s), top={_fact_top_score:.3f} < "
                f"working_fact_min_score={fact_min_score}; "
                f"fact_link path disabled for this query"
            )
        elif workspace_id and not fact_hits:
            # 作用域过滤把事实层清空（或这个 workspace 本来就没事实）。后果与上面
            # 那种一样：顺链断源，本次检索退化成只有经验直查的单层。结果里看不出
            # 来，只能在这里报。
            logger.warning(
                f"[search-coding] fact layer empty under scope "
                f"workspace_id={workspace_id!r} branch={branch!r}; "
                f"fact_link path disabled for this query"
            )
        # 事实层多取了几条（top 有 5 的下限，且阈值会砍掉一部分），这里落回 limit。
        # 顺链要基于最终返回的事实来走，否则会出现"经验标了 via=fact_link，但担保它的
        # 那条事实没在结果里"。
        fact_hits = fact_hits[:limit]

        out_facts: List[Dict[str, Any]] = []
        linked_ids: List[str] = []
        _seen_linked = set()
        for fact, score in fact_hits:
            out_facts.append({
                "fact_id": fact.fact_id,
                "statement": fact.statement,
                "files": list(fact.files),
                # 会话主体。覆盖闸门用它，检索侧带出来是为了让消费方能分辨
                # "这几条讲的是不同的人/文件"，而不是把它们读成同一件事的复述。
                "subject": fact.subject,
                # 被 statement 摘要掉的原文（计划表、配置块、命令输出）。摘要负责
                # 被召回，这一段负责召回之后真的有那个值——点查类问题要的是格子里的
                # 取值，不是"这次做了什么"的概述。不参与检索。
                "details": fact.details,
                # LLM 写的"这个结论一路怎么改成现在这样"，直接进提示词；
                # 没被覆盖过就是空串。不参与检索——召回认的是 statement 的当前值。
                "fact_chain_summary": fact.fact_chain_summary,
                "created_at": fact.created_at.isoformat() if fact.created_at else None,
                "score": round(float(score), 4),
                "source": fact.source,
            })
            # ---- 2. 顺链带出经验（按事实分数序，多事实指向同一经验只留首次） ----
            for eid in fact.derived_experience_ids:
                if eid not in _seen_linked:
                    _seen_linked.add(eid)
                    linked_ids.append(eid)
        # 这里不截断。id 列表里可能混着死链（经验被 DELETE，事实上的引用还留着），
        # 死了几条只有 get_many 查过才知道，任何固定余量都可能被吃穿。统一等取完
        # 详情、死链自然掉队之后再按 limit 截一次。
        # 长度天然有界：facts 已经 ≤ limit，每条的 derived_experience_ids 是个位数。

        # ---- 3. 经验直查兜底 ----
        try:
            _t_vdb = time.perf_counter()
            hits = await _store.search_by_query_embedding(
                embedding,
                user_id=primary_user,
                agent_ids=agent_ids,
                session_ids=session_ids,
                workspace_id=workspace_id,
                branch=branch,
                top=max(limit * 4, 8),  # 过采样为 dedup 留余量
                include_sources=include_sources,
                exclude_sources=exclude_sources,
            )
        except Exception as e:
            logger.warning(f"[search-coding] vdb search failed: {e}")
            hits = []
            _t_vdb = time.perf_counter()

        _raw_hits = len(hits)
        _top_scores = [h.get("score", 0.0) for h in hits[:10]]
        # 过滤前先把分数留一份：顺链那批经验自己没有分（它们是顺 id 拉出来的，
        # 不是搜出来的），要卡阈值只能借这一轮的分。同一个 query 向量，不额外调用。
        score_by_id = {h["experience_id"]: h.get("score", 0.0) for h in hits}
        hits = [h for h in hits if h.get("score", 0.0) >= exp_min_score]
        # 同上，不在这里截。长度由上面 VDB 的 top 兜着。

        # ---- 2b. 顺链那批也要过闸门 ----
        # 早期这一路是免检的（理由是"相关性由命中的事实担保"），但担保太弱：
        # 一条事实可能在某个侧面匹上 query，却把它另一个侧面的经验一起拖进来。
        # 现在跟直查同一道闸门：两边卡的本来就是同一批分数，分两档没有依据。
        # 分数取不到的（没出现在直查的 top-N 里）视为不合格：连前 N 都进不去，
        # 相似度必然低于第 N 名，正常语料下够不着闸门。
        #
        # 同阈值意味着顺链不再额外放行任何经验（能过闸的必然也在 direct_ids 里），
        # 它的作用退成排序：顺链的排在前面，limit 截断时先保住。
        linked_ids = [e for e in linked_ids
                      if score_by_id.get(e, 0.0) >= exp_min_score]
        linked_set = set(linked_ids)
        if _tracer is not None:
            await _tracer.log_coding_vdb(
                query=target_query, raw_hits=_raw_hits, kept_hits=len(hits),
                min_score=exp_min_score, top_scores=_top_scores,
                workspace_id=workspace_id, branch=branch,
                elapsed_ms=(time.perf_counter() - _t_vdb) * 1000,
            )

        direct_ids = [h["experience_id"] for h in hits]
        hit_by_id = {h["experience_id"]: h for h in hits}

        # ---- 4. 去重 + 存在性过滤 ----
        # 顺链在前、直查在后；get_many 查不到的（经验已被 DELETE / 两库不一致）
        # 自然被丢掉，死链不用单独处理
        ordered_ids = linked_ids + [e for e in direct_ids if e not in linked_set]
        # 纯安全阀：get_many 走 SQLite 的 IN 子句，绑定变量个数有上限（默认 999）。
        # 正常量级远够不到，这里只防病态长的链。语义上的条数控制在下面按 limit 做。
        ordered_ids = ordered_ids[:_CODING_GET_MANY_MAX_IDS]
        try:
            details = await _store.get_many(
                ordered_ids, user_id=primary_user,
                include_sources=include_sources,
                exclude_sources=exclude_sources,
            )
        except Exception as e:
            logger.warning(f"[search-coding] fetch details failed: {e}")
            details = []
        # 唯一一道语义截断。放在 get_many 之后：死链到这一步已经掉队了，
        # 先截的话它们会白占名额，实际返回数够不到 limit。
        # 顺链在前，所以砍掉的优先是直查那批——有事实背书的更该留。
        details = details[:limit]

        # ---- 5. 标注来源 ----
        out_experiences: List[Dict[str, Any]] = []
        for d in details:
            h = hit_by_id.get(d.experience_id) or {}
            out_experiences.append({
                "experience_id": d.experience_id,
                "agent_id": getattr(d, "agent_id", "") or "",
                "task": d.task,
                "search_keys": list(d.search_keys),
                "solution": d.solution,
                "boundary_envs": d.boundary_envs,
                "workspace_id": d.workspace_id,
                "branch": d.branch,
                "files": list(d.files),
                "source_fact_ids": list(d.source_fact_ids),
                "via": "fact_link" if d.experience_id in linked_set else "direct",
                "matched_key": h.get("matched_key_text"),
                "matched_key_kind": h.get("matched_key_kind"),
                # 顺链那批不在 hit_by_id 里（分数够不上直查阈值才需要顺链救），
                # 但 score_by_id 留了它们过滤前的分，这里透出来
                "score": (round(float(score_by_id[d.experience_id]), 4)
                          if d.experience_id in score_by_id else None),
                "confidence": d.confidence,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                "source": d.source,
                "extraction_source": d.extraction_source,
            })

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[search-coding] facts={len(out_facts)} experiences={len(out_experiences)} "
            f"(fact_link={sum(1 for m in out_experiences if m['via'] == 'fact_link')}) "
            f"elapsed_ms={elapsed} workspace_id={workspace_id!r} branch={branch!r}"
        )
        if _tracer is not None:
            await _tracer.log_coding_result(
                query=target_query,
                returned=len(out_experiences),
                # memory_ids 是 pipeline_logs 的通用列名（与 chat 路径共用），
                # 这里装的是 coding 侧的 experience_id
                memory_ids=[m["experience_id"] for m in out_experiences],
                previews=[
                    {"experience_id": m["experience_id"], "task": (m.get("task") or "")[:120],
                     "score": m.get("score"), "via": m.get("via")}
                    for m in out_experiences[:10]
                ],
                elapsed_ms=elapsed,
            )
        resp = {
            "request_id": request_id,
            "scene": "productivity",
            "facts": out_facts,
            "experiences": out_experiences,
            "elapsed_ms": elapsed,
        }
        # 版本演进链块（scene="coding" 独有）。放在最后：它吃的是上面已经定稿的
        # out_facts，链层不参与召回和排序。
        #
        # 注意返回形状：块单独放在 "chain_block" 字段，**不**把挂上链的 fact 从
        # out_facts 里删掉。不同调用方可能自行拼 prompt，悄悄少给 facts 比多给一个
        # 字段危险得多；需要“块 + 剩余 facts”时可按 chain_used 自行过滤。
        if variant == "coding":
            _blk, _used = self._decorate_with_chain(
                out_facts, instruction=target_query,
                scope=(workspace_id or None),
                user_id=primary_user, workspace_id=workspace_id,
            )
            resp["chain_block"] = _blk
            resp["chain_used"] = _used
        return resp

    # ================================================================
    # 同步 API
    # ================================================================

    def add(
        self,
        data: AddInput,
        *,
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        source: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Optional[datetime] = None,
        scene: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        embed_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        写入记忆。

        处理深度由 Client 初始化时的 mode 决定：
        - lite:  纯 embedding 写入
        - pro:   embedding + LLM agent 提取（事实/实体/reconcile）
        - ultra: pro + 可通过 evolve() 显式触发的 System 2 认知加工

        Args:
            data: 文本字符串，或 messages 列表
                  - "用户喜欢科幻电影"
                  - [{"role": "user", "content": "..."}, ...]
            user_id:      用户 ID（一级隔离 key）
            agent_id:     Agent ID（二级隔离 key）
            session_id:   Session ID（三级隔离 key）
            source:       调用来源，例如 openclaw / cursor / codex；与 agent_id 正交。
                          写入前执行 strip().lower()，默认 "default"。
            metadata:     旧版 SDK 处理选项，会并入内部 WriteRequest.extra。
                          不保证持久化或随查询返回；调用方需要保存并读回的业务字段
                          请使用 meta。
            memory_at:    记忆时间戳（不传则用当前时间），用于导入历史记忆
            scene:        显式指定写入路由，跳过 LLM 自动判定。
                          - None（默认）：含 tool 时 LLM 判 chat/working/coding；
                            无 tool 时直接走 chat
                          - "chat"：强制走 chat 链路（tool 消息会被 strip）
                          - "working"：强制走 coding 链路（通用生产力场景）
                          - "coding"：强制走 coding 链路（软件工程场景）
                          注意：强制 working / coding 时输入必须含 tool 消息，
                          否则回退 chat 链路。

                          "working" 与 "coding" 复用同一种生产力 pipeline 和表结构，
                          但使用独立 SQLite 文件与向量集合。行为差异为：
                            1. 抽取规范不同。coding 用 EXTRACT_PROMPT_CODING：要求
                               分支名逐字保留、说明哪一版赢了哪一版被弃、给精确的
                               仓相对路径 + 符号名、区分"已验证"与"只是写了"。
                            2. coding 额外维护版本演进链层（零 LLM 的 sqlite），存下
                               每处代码每一版的真实 patch。补的是抽取规范本身砍掉的
                               那类信息 —— 它明文要求"给坐标不要贴代码"，而被撤掉那
                               版的代码不在工作区也不在 HEAD，读不回来。
                          搜索侧要拿到链块必须也传 scene="coding"，见 search()。
            workspace_id: Coding memory 路径专用。repo 标识（建议 git remote URL 规范化）。
                          随经验与事实一起落库，供 search / list 按作用域筛选。chat 路径忽略。
            branch:       Coding memory 路径专用。分支名。同 workspace_id。chat 路径忽略。
            meta:         调用方自定义业务 meta，随本次产出的记忆存储、查询时原样返回。
                          不 embed、不参与召回、不作过滤条件。
                          与 metadata 不同：metadata 是旧版内部处理选项，
                          meta 独立持久化，只做业务透传。
                          语义为「该次 write 时刻的上下文」，故存为 list——同一节点被
                          多次写入合并时会追加而非覆盖，详见 get() 返回的 meta 字段。

        Returns:
            chat 链路：{"success": True, "memory_id": "...", "request_id": "...", "elapsed_ms": ...}
            coding 链路：{"success": True, "scene": "productivity", "ops": [...],
                        "experience_ids": [...], "fact_ops": [...], "fact_ids": [...], ...}
        """
        return self._loop_thread.run(
            self.async_add(
                data, user_id=user_id, agent_id=agent_id, session_id=session_id,
                source=source,
                metadata=metadata, memory_at=memory_at,
                scene=scene,
                workspace_id=workspace_id, branch=branch,
                request_id=request_id, meta=meta,
                embed_override=embed_override,
            ),
            include_runtime_diag=True,
        )

    def finalize_session(
        self,
        *,
        user_id: str,
        agent_id: str = "default_agent",
        session_id: str,
    ) -> Dict[str, Any]:
        """Finalize one caller session into a first-class episode narrative.

        The feature is opt-in through MEMORY_SESSION_EPISODE_ENABLED. Calls are
        idempotent: the same user/agent/session tuple upserts one deterministic node.
        """
        return self._loop_thread.run(
            self.async_finalize_session(
                user_id=user_id, agent_id=agent_id, session_id=session_id,
            ),
            include_runtime_diag=True,
        )

    def search(
        self,
        query: Optional[str] = None,
        *,
        queries: Optional[List[str]] = None,
        scene: Optional[str] = None,
        search_mode: str = "default",
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        limit: int = 10,
        min_score: float = 0.4,
        working_fact_min_score: Optional[float] = None,
        working_experience_min_score: Optional[float] = None,
        schema_min_score: float = 0.4,
        schema_limit: int = 10,
        intention_limit: int = 0,
        created_after: Optional[float] = None,
        reader: str = "",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        layers: Optional[List[Any]] = None,
        embed_override: Optional[Dict[str, Any]] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """
        搜索记忆。

        三路召回（chat 路径）：
        - **Schema 路**: L6_SCHEMA（高层行为/状态 schema）
        - **Proactive 路**: L7_INTENTION（主动意图，默认关闭）
        - **Normal 路**: L2_FACT + L3_SUMMARY + L4_IDENTITY + L5_KNOWLEDGE 等

        用户画像 L0 不再混入 Search；请调用 get_user_profile() 单独获取。

        Coding 路径（自动判定）：
        - 当 queries 上下文判定为 coding 时，走 CodingMemoryStore 的双层召回：
          事实层为主索引，命中的事实顺 derived_experience_ids 带出经验，再用同一
          向量直查经验层兜底。返回结构含 "scene": "productivity" 和 "facts" /
          "experiences" 两个列表。
        - 否则走 chat 路径，返回与历史完全一致。

        Args:
            query:              搜索查询（兼容旧调用；与 queries 二选一）
            queries:            多条 queries（最后一条为 target；前置项用于上下文消解）。
                                default 模式下，Coding 开启时分类并 rewrite；Coding 关闭但
                                Query Rewrite 开启时只 rewrite/time；两者关闭时零 LLM。
            scene:              显式指定检索路由，跳过 LLM 自动判定。与 add() 取值一致：
                                - None（默认）：由 search_mode 决定是否自动判定
                                - "chat"：强制走 chat 召回
                                - "working"：强制走 coding（生产力）召回
                                - "coding"：同上，额外返回版本演进链块
                                  （"chain_block" / "chain_used" 两个字段，见下方 Returns）
                                （旧别名 "normal"/"productivity" 仍兼容）
            search_mode:        搜索执行模式：
                                - "default"（默认）：query="..." 是 light search，直接
                                  chat/normal、零 LLM；显式传 queries=[...] 时由 Coding 与
                                  Query Rewrite 开关共同决定是否分类/改写。
                                - "agentic"：scene=None 时，单 query / 多 queries 都用
                                  一次 LLM 完成三分类、query rewrite 和时间范围解析。
                                显式 scene 始终直接路由，不调用 LLM。
            user_ids:           用户 ID 列表（一级隔离 key，支持跨用户搜索）
            agent_ids:          Agent ID 列表（空列表 = 搜索所有 agent）
            session_ids:        Session ID 列表（空列表 = 搜索所有 session）
            include_sources:    调用来源白名单；空列表 = 不限。
            exclude_sources:    调用来源黑名单；与白名单冲突时黑名单优先。
            limit:              Normal 路的 topk 数量，默认 10
            min_score:          **chat 路专用**的最低分数阈值，默认 0.4。working 路不看
                                这个值，两层各有自己的阈值（见下两项）。
            working_fact_min_score:
                                working 路事实层（主索引）的阈值，默认 0.30。刻意低于
                                chat：事实是一句话结论，同样相关度下分数系统性偏低，沿用
                                0.4 会挡掉明确相关的事实，还会连带把顺链带经验那一路掐断。
            working_experience_min_score:
                                working 路经验层的阈值，顺链和直查共用。经验层 embed
                                的是 task + search_keys 这类短片段，相似度基线高于事实
                                层的整句 statement，跟事实共用一个阈值会让经验明显偏松，
                                所以不传则取 working_fact_min_score + 0.10。
            schema_min_score:   Schema 路的最低分数阈值，默认 0.4
            schema_limit:       L6 Schema 路的最大返回数，默认 10
            intention_limit:    Proactive 路的 topk 数量，默认 0（关闭）
            created_after:      Unix timestamp (float)，只返回 gmt_created >= 此值的记忆
            reader:             Reader 类型，动态切换。仅支持 legacy / hybrid。
                                空字符串 = 使用环境变量 HY_MEMORY_READER 或默认 legacy。
            workspace_id:       Coding 路径专用（chat 路径忽略）。作用域筛选条件：不传则
                                不筛（全量语义召回），传了则事实层与经验层都只在该
                                workspace 内召回。过滤在 SQLite 侧做，向量取数按作用域
                                选择率放大，不会因为过滤掉召回。
            branch:             同 workspace_id，但只在 workspace_id 非空时才叠加（与
                                list_memories 一致），单独传不生效。容 NULL：没记录分支
                                的经验/事实照样召回，否则老数据会被整片筛掉。
            request_id:         可选。App 层预生成以对齐 SEARCH_API_ACCEPTED；不传则 SDK 自生成
            layers:             可选。pro/ultra 下只召回指定层级（字符串或
                                MemoryLayer），在召回阶段生效。lite 下忽略该参数并
                                强制只召回 L1_RAW。

        Returns:
            chat 路径：{"request_id": "...", "memories": [...], "elapsed_ms": ...}
            coding 路径：{"request_id": "...", "scene": "productivity",
                         "facts": [...], "experiences": [...], "elapsed_ms": ...}
            scene="coding" 额外带两个字段：
                "chain_block" —— 版本演进链块（多行文本，挂不上时为 ""）。
                                 该放在 facts 之前给模型读。
                "chain_used"  —— 已挂进 chain_block 的 facts 下标列表。
                                 facts **没有**被去重过（不悄悄少给）；需要
                                 "块 + 剩余 fact" 的拼法就按这个列表自行过滤。
        """
        return self._search_loop_thread.run(self.async_search(
            query, queries=queries, scene=scene, search_mode=search_mode,
            user_ids=user_ids, agent_ids=agent_ids, session_ids=session_ids,
            include_sources=include_sources, exclude_sources=exclude_sources,
            limit=limit, min_score=min_score,
            working_fact_min_score=working_fact_min_score,
            working_experience_min_score=working_experience_min_score,
            schema_min_score=schema_min_score, schema_limit=schema_limit,
            intention_limit=intention_limit, created_after=created_after,
            reader=reader,
            workspace_id=workspace_id, branch=branch,
            request_id=request_id, layers=layers,
            embed_override=embed_override,
            query_embedding=query_embedding,
        ))

    def get(
        self,
        memory_id: str,
        *,
        include_raw: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        获取单条记忆。

        Args:
            memory_id: 记忆 ID
            include_raw: 是否允许返回 L1_RAW 原始记忆。默认 False，保持原有隐藏行为；
                         lite 模式的记忆均为 L1_RAW，需显式设为 True 才能按 ID 读取。

        Returns:
            {"memory_id", "content", "layer", "request_id", "elapsed_ms"} 或 None（不存在时）
        """
        return self._loop_thread.run(
            self.async_get(memory_id, include_raw=include_raw)
        )

    def update(
        self,
        memory_id: str,
        content: str,
    ) -> Dict[str, Any]:
        """
        更新记忆内容。

        Args:
            memory_id: 记忆 ID
            content:   新内容

        Returns:
            {"success": True, "memory_id": "...", "request_id": "...", "elapsed_ms": ...}
        """
        return self._loop_thread.run(self.async_update(memory_id, content))

    def delete(self, memory_id: str) -> Dict[str, Any]:
        """
        删除单条记忆。

        Args:
            memory_id: 记忆 ID

        Returns:
            {"success": True, "deleted_count": 1, "request_id": "...", "elapsed_ms": ...}
        """
        return self._loop_thread.run(self.async_delete(memory_id))

    def delete_all(
        self,
        *,
        user_id: str = "",
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        删除用户的记忆。

        默认删除该用户的所有记忆（agent_ids/session_ids 都不传时）。
        可通过 agent_ids/session_ids 缩小删除范围。

        Args:
            user_id:      用户 ID（一级隔离 key）
            agent_ids:    Agent ID 列表（空列表 = 所有 agent）
            session_ids:  Session ID 列表（空列表 = 所有 session）

        Returns:
            {"success": True, "deleted_count": N, "request_id": "...", "elapsed_ms": ...}
        """
        return self._loop_thread.run(
            self.async_delete_all(user_id=user_id, agent_ids=agent_ids, session_ids=session_ids)
        )

    def list_memories(
        self,
        *,
        user_id: str = "",
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List[Any]] = None,
        with_evolution: bool = False,
    ) -> Dict[str, Any]:
        """
        列出用户的记忆（同步）。

        从向量库直接读取，只返回 ACTIVE 状态的记忆。
        按 created_at 排序，支持分页。

        Args:
            user_id:  用户 ID
            agent_id: 可选，限定某个 agent
            limit:    每页条数（默认 100）
            offset:   偏移量（默认 0）
            order:    排序方式，"desc"（默认，最新在前）或 "asc"
            workspace_id: 可选（默认空）。非空则直接走 coding memory 列表路径，
                          按 workspace_id 过滤，返回 {"scene": "productivity", ...}。
            branch:   可选（默认空）。仅在 workspace_id 非空时叠加 branch 精确过滤。
            user_ids: 可选，多用户批量列举。非空时优先于 user_id，返回并集
                      （分页在合并排序后统一施加）。
            agent_ids: 可选，多 agent 批量列举。非空时优先于 agent_id。
            layers:   可选，只返回指定层级（字符串或 MemoryLayer）。不传时返回
                      L1_RAW 以外的全部层级，与改动前一致。
            with_evolution: 可选，为当前页条目附加 evolution_chain（默认关闭）。

        Returns:
            chat 路径: {"vdb": {...}, "graph": {...} | 省略, "elapsed_ms": ...}
            coding 路径: {"scene": "productivity", "experiences": [...], ...}
        """
        return self._loop_thread.run(
            self.async_list_memories(
                user_id=user_id, agent_id=agent_id,
                user_ids=user_ids, agent_ids=agent_ids,
                limit=limit, offset=offset, order=order,
                workspace_id=workspace_id, branch=branch,
                layers=layers, with_evolution=with_evolution,
            )
        )

    def get_user_basic_info(
        self,
        *,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """返回用户的 L0 basic info 记忆（同步）。

        存在演化链时以链头为代表返回并附 `evolution_chain`，与 search / list 一致。

        Returns:
            {"request_id", "memories": [...], "elapsed_ms"}
        """
        return self._loop_thread.run(
            self.async_get_user_basic_info(user_id=user_id)
        )

    def get_user_profile(
        self,
        *,
        user_id: str,
    ) -> Dict[str, Any]:
        """Return the user's single assembled L0 profile.

        This is the public replacement for the L0 row previously mixed into
        Search results. It performs no embedding or LLM call.
        In Ultra mode, the active balanced catalog and specific supplement are
        appended to the same profile content.

        Returns:
            {"request_id": str, "profile": dict | None, "elapsed_ms": float}
        """
        return self._loop_thread.run(
            self.async_get_user_profile(user_id=user_id)
        )

    def clone_user(
        self,
        src_user_id: str,
        dst_user_id: str,
        *,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        克隆用户记忆。

        将 src_user_id 下的全部记忆深拷贝到 dst_user_id 下，
        两者此后完全独立互不影响。

        Args:
            src_user_id: 源用户 ID
            dst_user_id: 目标用户 ID
            agent_id:    可选，限定克隆某个 agent 下的记忆

        Returns:
            {"success": True, "request_id": "...", "cloned_count": N,
             "by_layer": {"l0_basic_info": 1, ...}, "elapsed_ms": ...}
        """
        return self._loop_thread.run(
            self.async_clone_user(
                src_user_id, dst_user_id, agent_id=agent_id,
            )
        )

    # ================================================================
    # System 2 Evolve API
    # ================================================================

    def evolve(
        self,
        user_id: str,
        agent_id: str = "default_agent",
        max_batches: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        手动触发 System 2 认知加工（ultra 模式专用，同步阻塞直到完成）。

        evolve 一次 = 对该 user 的 VDB 全量跑一次 System 2（Schema 归纳 → L6_SCHEMA
        + cross-domain sweeper）。System 2 的输入是 VDB 全量扫描，与「期间 add 了几次」
        无关，因此 evolve 不消费写入队列、跑一次完整认知周期。

        非幂等：每次 evolve 看到的是当前最新 VDB 全量，连续多次 evolve 有意义
        （add 一批 → evolve → 再 add 一批 → 再 evolve）。

        注意：意图（L7_INTENTION）由 System 1 在 add/write 时即时抽取落库（pro/ultra
        模式都有），不在 evolve 里处理。evolve 只负责 System 2 的 Schema 层。

        触发时机由调用方控制：System 2 不在 add/write 里自动触发，也无进程内队列。
        典型用法：批量 add()（只跑 S1）→ 调一次 evolve() 做全量 Schema 归纳。

        仅 ultra 模式可调用。lite/pro 模式调用会抛出 RuntimeError。

        Args:
            user_id:  用户 ID
            agent_id: Agent ID

        Returns:
            {"success": True, "request_id": "...", "tasks_processed": 1, "results": {...},
             "cross_domain_sweeper": {...}, "elapsed_ms": ...}
        """
        if self._mode != "ultra":
            raise RuntimeError(
                f"evolve() requires mode='ultra', but this client is mode='{self._mode}'. "
                f"Create a new client with HyMemoryClient(mode='ultra') to use System 2."
            )
        return self._loop_thread.run(
            self.async_evolve(
                user_id=user_id, agent_id=agent_id, max_batches=max_batches,
            )
        )

    @_ensure_internal_loop
    async def async_evolve(
        self,
        user_id: str,
        agent_id: str = "default_agent",
        max_batches: Optional[int] = None,
    ) -> Dict[str, Any]:
        """手动触发 System 2 认知加工（异步）"""
        t0 = __import__("time").perf_counter()

        # 获取 System2Writer
        try:
            writer = await self._registry.get_system2_writer()
        except (ValueError, KeyError):
            return {
                "success": False,
                "error": "System2Writer not available.",
                "tasks_processed": 0,
            }

        # 检查是否支持 evolve
        if not hasattr(writer, "evolve"):
            return {
                "success": False,
                "error": f"Writer '{writer.VERSION}' does not support evolve()",
                "tasks_processed": 0,
            }

        result = await writer.evolve(
            user_id=user_id, agent_id=agent_id, max_batches=max_batches,
        )
        result["elapsed_ms"] = round((__import__("time").perf_counter() - t0) * 1000, 2)
        return result

    def sweep(
        self,
        user_ids: List[str],
        agent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        手动触发 cross-domain sweeper（同步）。

        独立于 evolve 流程，不执行 S2 Agent，只做：
        1. 补齐 schema 的 beh abstraction + beh_embedding
        2. 碰撞扫描 + core 归纳

        对每个 user_id × agent_id 组合执行一次 sweeper。

        Args:
            user_ids:  SDK user_id 列表
            agent_ids: Agent ID 列表（默认 ["default_agent"]）

        Returns:
            {"success": True, "results": [{...}, ...]}
        """
        if self._mode != "ultra":
            raise RuntimeError(
                f"sweep() requires mode='ultra', but this client is mode='{self._mode}'."
            )
        return self._loop_thread.run(
            self.async_sweep(user_ids=user_ids, agent_ids=agent_ids)
        )

    @_ensure_internal_loop
    async def async_sweep(
        self,
        user_ids: List[str],
        agent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """手动触发 cross-domain sweeper（异步）

        对每个 user_id × agent_id 组合执行一次 sweeper。
        """
        t0 = __import__("time").perf_counter()

        try:
            writer = await self._registry.get_system2_writer()
        except (ValueError, KeyError):
            return {"success": False, "error": "System2Writer not available.", "results": []}

        effective_agents = agent_ids or ["default_agent"]
        results = []
        for uid in user_ids:
            for aid in effective_agents:
                r = await writer.run_sweeper(user_id=uid, agent_id=aid)
                results.append({"user_id": uid, "agent_id": aid, **r})

        elapsed_ms = round((__import__("time").perf_counter() - t0) * 1000, 2)
        return {"success": True, "results": results, "elapsed_ms": elapsed_ms}

    # ================================================================
    # System Metrics
    # ================================================================

    def get_metrics(self, minutes: int = 5) -> Dict[str, Any]:
        """
        获取系统整体负载指标。

        Args:
            minutes: 查询最近 N 分钟的数据（默认 5 分钟）

        Returns:
            {
                "uptime_seconds": float,
                "requests": {"total", "active_sys1", "active_sys2", "queued_sys2", "completed", "failed"},
                "avg_latency_ms": {"sys1_waiting", "sys1_l1_process", "sys1_workflow", ...},
                "throughput": {"sys1_completed_last_60s", "sys2_completed_last_60s"},
                "storage_ops": {"vdb_ops_total", "vdb_ops_avg_ms", "graph_ops_total", "graph_ops_avg_ms"},
            }
        """
        return self._loop_thread.run(self.async_get_metrics(minutes=minutes))

    @_ensure_internal_loop
    async def async_get_metrics(self, minutes: int = 5) -> Dict[str, Any]:
        """获取系统整体负载指标（异步）"""
        from .metrics import MetricsCollector
        return await MetricsCollector.get().get_snapshot(minutes=minutes)

    def close(self):
        """关闭客户端，释放所有资源"""
        if self._registry is not None:
            self._loop_thread.run(self._close_async())
        # solo 模式：本 client 拥有 loop_thread，可以安全 stop
        # shared 模式：loop_thread 由 runtime 管，留给 runtime.aclose() 关
        if hasattr(self, '_loop_thread') and self._owns_runtime:
            self._loop_thread.stop()

    def history(self, memory_id: str) -> List[Dict[str, Any]]:
        """
        查询某条记忆的变更历史。

        Args:
            memory_id: 记忆 ID

        Returns:
            按时间排序的历史记录列表，每条包含 event/old_memory/new_memory/created_at 等字段。
            如果未启用历史记录，返回空列表。
        """
        return self._loop_thread.run(self.async_history(memory_id))

    def get_recent_history(
        self,
        user_id: str = "",
        agent_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询最近的操作历史。

        Args:
            user_id:    用户 ID
            agent_ids:  Agent ID 列表（空列表 = 查所有 agent 的历史）
            limit:      返回数量上限

        Returns:
            按时间倒序的最近操作列表。
            如果未启用历史记录，返回空列表。
        """
        return self._loop_thread.run(
            self.async_get_recent_history(user_id=user_id, agent_ids=agent_ids, limit=limit)
        )

    async def _close_async(self):
        if self._owns_runtime:
            from .metrics import MetricsCollector

            await MetricsCollector.get().stop_background_tasks()

        # 先关 coding 链路（不依赖其他组件）
        # ⚠️ 遍历 _coding_stores 而不是只关 self._coding_store —— 后者只是"最近一次
        #    用的那份"，两个变体混用过时另一份会漏掉（sqlite 连接和向量库句柄泄漏）。
        for _v, _st in list(getattr(self, "_coding_stores", {}).items()):
            try:
                await _st.close()
            except Exception as e:
                logger.warning(f"[close] coding store close failed (variant={_v}): {e}")
        if getattr(self, "_coding_stores", None) is not None:
            self._coding_stores.clear()
        if getattr(self, "_coding_writers", None) is not None:
            self._coding_writers.clear()
        if getattr(self, "_coding_store", None) is not None:
            self._coding_store = None
            self._coding_writer = None
            self._coding_llm_provider = None
        # 链层：纯 sqlite，没有异步资源
        if getattr(self, "_chain_store", None) is not None:
            try:
                self._chain_store.close()
            except Exception as e:
                logger.warning(f"[close] chain store close failed: {e}")
            self._chain_store = None
        if self._registry:
            await self._registry.close()
            self._registry = None
        if self._graph_store:
            await self._graph_store.close()
            self._graph_store = None
        if self._history_store:
            await self._history_store.close()
            self._history_store = None
        # cache：solo 模式下 client 拥有，关；shared 模式下 runtime 拥有，留给 runtime.aclose()
        if self._cache and self._owns_runtime:
            await self._cache.close()
            self._cache = None
        elif self._cache and not self._owns_runtime:
            # shared 模式：仅断引用，不真正关 pool
            self._cache = None
        logger.info("HyMemoryClient closed")

    # ================================================================
    # Delete helpers — 演化链修复 + Graph evidence 清理
    # ================================================================

    async def _repair_evolution_chain_on_delete(self, node) -> None:
        """
        删除节点前修复演化链。

        场景 1: 删除链头（is_latest=True + supersedes 非空）
          → 将 supersedes 列表中的前驱恢复为 is_latest=True + ACTIVE
          → 从前驱的 superseded_by 中移除本节点 ID

        场景 2: 删除链中间节点（is_latest=False + superseded_by 非空 + supersedes 非空）
          → 将前驱的 superseded_by 改为指向后继
          → 将后继的 supersedes 改为指向前驱（跳过本节点）

        场景 3: 删除链尾（is_latest=False + supersedes 为空）
          → 只需从后继的 supersedes 中移除本节点 ID
        """
        from .models.memory import MemoryStatus

        node_id = node.node_id
        supersedes = node.supersedes or []        # 本节点取代的旧节点
        superseded_by = node.superseded_by or []  # 取代本节点的新节点
        is_latest = node.is_latest

        if not supersedes and not superseded_by:
            return  # 不在任何链上

        try:
            # 场景 1: 删除链头 — 恢复前驱
            if is_latest and supersedes:
                for pred_id in supersedes:
                    pred = await self._vector_store.get_by_id(pred_id)
                    if pred is None:
                        continue
                    # 从前驱的 superseded_by 中移除本节点
                    pred_sb = [x for x in (pred.superseded_by or []) if x != node_id]
                    # 如果前驱没有其他后继，恢复为链头
                    if not pred_sb:
                        await self._vector_store.update_payload(pred_id, {
                            "is_latest": True,
                            "status": MemoryStatus.ACTIVE.value,
                            "superseded_by": [],
                        })
                        logger.info(f"[delete] chain repair: restored {pred_id} as chain head")
                    else:
                        await self._vector_store.update_payload(pred_id, {
                            "superseded_by": pred_sb,
                        })

            # 场景 2: 删除链中间 — 跳接前驱和后继
            elif not is_latest and supersedes and superseded_by:
                # 后继的 supersedes: 把本节点替换为本节点的前驱
                for succ_id in superseded_by:
                    succ = await self._vector_store.get_by_id(succ_id)
                    if succ is None:
                        continue
                    succ_sup = list(succ.supersedes or [])
                    if node_id in succ_sup:
                        succ_sup.remove(node_id)
                        succ_sup.extend(supersedes)
                    await self._vector_store.update_payload(succ_id, {
                        "supersedes": succ_sup,
                    })

                # 前驱的 superseded_by: 把本节点替换为本节点的后继
                for pred_id in supersedes:
                    pred = await self._vector_store.get_by_id(pred_id)
                    if pred is None:
                        continue
                    pred_sb = list(pred.superseded_by or [])
                    if node_id in pred_sb:
                        pred_sb.remove(node_id)
                        pred_sb.extend(superseded_by)
                    await self._vector_store.update_payload(pred_id, {
                        "superseded_by": pred_sb,
                    })
                logger.info(f"[delete] chain repair: bridged {supersedes} → {superseded_by}")

            # 场景 3: 删除链尾 — 从后继的 supersedes 中移除
            elif not is_latest and not supersedes and superseded_by:
                for succ_id in superseded_by:
                    succ = await self._vector_store.get_by_id(succ_id)
                    if succ is None:
                        continue
                    succ_sup = [x for x in (succ.supersedes or []) if x != node_id]
                    await self._vector_store.update_payload(succ_id, {
                        "supersedes": succ_sup,
                    })

        except Exception as e:
            logger.warning(f"[delete] chain repair failed for {node_id}: {e}")

    async def _cleanup_graph_evidence(self, deleted_node_id: str) -> None:
        """
        清理已删除 VDB 节点在 Graph 中的 evidence 引用。

        当一个 VDB 节点被删除后，如果它作为 VdbRef 影子节点被某个
        Schema/Intention 通过 DERIVED_FROM 引用，需要清理悬空引用。

        注意：VdbRef 是独立的 node table，不能用 delete_node（那是删 Memory）。
        直接用底层 execute 删 VdbRef + DERIVED_FROM 边。
        """
        if self._graph_store is None:
            return
        try:
            gs = self._graph_store
            # Kuzu backend
            if hasattr(gs, '_execute') and hasattr(gs, '_available'):
                if not gs._available:
                    return
                gs._execute(
                    "MATCH (v:VdbRef {node_id: $nid}) DETACH DELETE v;",
                    {"nid": deleted_node_id},
                )
            # Neo4j backend
            elif hasattr(gs, '_run_write'):
                await gs._run_write(
                    "MATCH (v:VdbRef {node_id: $nid}) DETACH DELETE v",
                    {"nid": deleted_node_id},
                )
            logger.debug(f"[delete] VdbRef {deleted_node_id} cleaned from Graph")
        except Exception as e:
            logger.debug(f"[delete] VdbRef cleanup for {deleted_node_id}: {e}")

    # ================================================================
    # 异步 API
    # ================================================================

    @_ensure_internal_loop
    async def async_add(
        self,
        data: AddInput,
        *,
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        source: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Optional[datetime] = None,
        scene: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        embed_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """写入记忆（异步）

        embed_override: 本次写入的 embedding 端点覆盖（不改 model/dims，只换接入点）。
            dict，可含 base_url / api_key / extra_headers / timeout；None = 用 client 配置。
            可用于按请求切换任意 OpenAI-compatible embedding endpoint。
        """
        if embed_override:
            with embed_endpoint_override(
                base_url=embed_override.get("base_url"),
                api_key=embed_override.get("api_key"),
                extra_headers=embed_override.get("extra_headers"),
                timeout=embed_override.get("timeout"),
            ):
                return await self._async_add_impl(
                    data, user_id=user_id, agent_id=agent_id, session_id=session_id,
                    source=source,
                    metadata=metadata, memory_at=memory_at,
                    scene=scene, workspace_id=workspace_id, branch=branch,
                    request_id=request_id, meta=meta,
                )
        return await self._async_add_impl(
            data, user_id=user_id, agent_id=agent_id, session_id=session_id,
            source=source,
            metadata=metadata, memory_at=memory_at,
            scene=scene, workspace_id=workspace_id, branch=branch,
            request_id=request_id, meta=meta,
        )

    @_ensure_internal_loop
    async def async_finalize_session(
        self,
        *,
        user_id: str,
        agent_id: str = "default_agent",
        session_id: str,
    ) -> Dict[str, Any]:
        """Async counterpart of :meth:`finalize_session`."""
        series = bool(getattr(self._config.recall, "series_episode_enabled", False))
        dynamic = bool(
            getattr(self._config.recall, "dynamic_episode_enabled", False) or series
        )
        if not (
            getattr(self._config.recall, "session_episode_enabled", False) or dynamic
        ):
            return {"success": False, "status": "disabled",
                    "error_message": "episode finalization is disabled"}
        if not user_id or not session_id:
            raise ValueError("user_id and session_id are required")
        from .agent.episode_summary import (
            finalize_dynamic_episode_capsules,
            finalize_series_episode_containers,
            finalize_session_episode,
        )
        writer = await self._registry.get_writer()
        vector_store = await writer._get_vector_store()
        finalize = (
            finalize_dynamic_episode_capsules if dynamic
            else finalize_session_episode
        )
        finalize_kwargs = dict(
            vector_store=vector_store,
            embed_service=writer.embed_service,
            llm_provider=writer.llm_provider,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        if finalize is finalize_dynamic_episode_capsules:
            finalize_kwargs["summary_concurrency"] = getattr(
                self._config.recall, "dynamic_episode_summary_concurrency", 3,
            )
        from .core.workload import episode_slot, workload_scope

        async with episode_slot(), workload_scope("derived"):
            result = await finalize(**finalize_kwargs)
        if series and result.get("status") != "failed":
            try:
                result["series_episode"] = await finalize_series_episode_containers(
                    vector_store=vector_store,
                    graph_store=self._graph_store,
                    user_id=user_id,
                    agent_id=agent_id,
                )
            except Exception as exc:
                logger.warning("[series-episode] finalize failed: %s", exc)
                result["series_episode"] = {
                    "status": "failed", "error": str(exc),
                }
        return {"success": result.get("status") != "failed", **result}

    async def _async_add_impl(
        self,
        data: AddInput,
        *,
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        source: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Optional[datetime] = None,
        scene: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """写入记忆（异步）

        Args:
            scene: 显式指定写入路由（"chat" / "working" / "coding"）；
                   None = 自动判定。详见 add() docstring。
            workspace_id / branch: Coding memory 路径专用，详见 add() docstring。
            meta: 调用方自定义业务 meta，透传存储，详见 add() docstring。
        """
        from .utils.source_filter import normalize_source
        source = normalize_source(source)
        # 归一化显式 scene（None = 自动判定）
        scene_forced = self._normalize_scene(scene)
        # coding 链路的变体（"working" | "coding"）。显式 scene 直接决定；
        # scene=None 时由三分类 judge 回填，不能默认吞成 working。
        scene_variant = self._scene_variant(scene)
        # 生成 request_id 并注入日志上下文（队列消费可传入固定 id 保证幂等与追溯）
        request_id = request_id or str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        content, chat_messages, content_type = self._parse_input(data)
        uid = user_id

        # 纯 input token 数（tiktoken 精确估算；不含 system/few-shot/历史等 prompt 开销）
        from .utils.token_count import count_input_tokens
        input_tokens = count_input_tokens(content, chat_messages)

        # ────────────────────────────────────────────────────────────
        # Coding 链路分流
        # scene 显式指定时跳过 LLM 判定：
        #   scene="chat"   → 强制 chat（strip tool 后走 chat 链）
        #   scene="coding" → 强制 coding（须含 tool 消息，否则回退 chat）
        # scene=None（默认）→ 自动三分类：
        #   不含 tool: O(1) 短路 → chat 链
        #   含 tool 但判 chat: strip tool 后走 chat 链
        #   含 tool 且判 working/coding: 走对应 variant 的生产力链路
        # 关键：不影响现有 chat 路径
        # ────────────────────────────────────────────────────────────
        coding_available = (
            getattr(self, "_mode", "pro") != "lite"
            and content_type == "messages"
            and getattr(self._config, "coding", None) is not None
            and self._config.coding.enable
        )

        if scene_forced == "chat":
            # 强制 chat：跳过一切 coding 判定，strip tool 消息后走 chat 链
            if content_type == "messages" and has_any_tool_message(chat_messages):
                logger.info("[add] scene=chat forced; stripping tool messages")
                chat_messages = strip_tool_messages(chat_messages)
        elif coding_available and (
            scene_forced == "coding" or has_any_tool_message(chat_messages)
        ):
            has_tool = has_any_tool_message(chat_messages)

            if scene_forced == "coding" and not has_tool:
                # 强制 coding 但无 tool 消息：无法抽 coding memory，回退 chat
                logger.warning(
                    "[add] scene=coding forced but no tool messages found; "
                    "falling back to chat path"
                )
                is_coding = False
            elif scene_forced == "coding":
                # 显式指定 coding：跳过 LLM judge
                logger.info("[add] scene=coding forced; skip LLM judge")
                is_coding = True
            else:
                # 自动判定（scene=None，含 tool 消息）
                # lazy 初始化 coding LLM provider（用于 judge）
                if self._coding_llm_provider is None:
                    from .agent.llm_provider import LLMProvider
                    self._coding_llm_provider = LLMProvider(self._config)
                try:
                    judged_scene = await classify_messages_scene(
                        chat_messages, self._coding_llm_provider,
                        cache=self._cache,
                        request_id=request_id,
                        user_id=uid,
                        agent_id=agent_id or "default_agent",
                        session_id=session_id or "",
                    )
                    is_coding = judged_scene != "chat"
                    if is_coding:
                        scene_variant = judged_scene
                except Exception as e:
                    logger.warning(f"[add] coding judge failed, fallback to chat: {e}")
                    is_coding = False

            if is_coding:
                logger.info(
                    f"[add] routing to PRODUCTIVITY path variant={scene_variant}: "
                    f"user={uid} agent_id={agent_id} "
                    f"workspace_id={workspace_id!r} branch={branch!r}"
                )
                writer = await self._get_coding_writer(variant=scene_variant)
                resp = await writer.write(
                    chat_messages,
                    user_id=uid,
                    agent_id=agent_id,
                    workspace_id=workspace_id,
                    branch=branch,
                    session_id=session_id,
                    request_id=request_id,
                    source=source,
                )
                # 版本演进链层（scene="coding" 独有）。放在 writer.write 之后、返回
                # 之前，因为它只依赖对话原文，与向量层写入结果无关。
                # best-effort：这层挂不上不影响本次 add 的向量层产出。
                if scene_variant == "coding":
                    resp["chain_rounds"] = self._observe_chain(
                        chat_messages, user_id=uid,
                        workspace_id=workspace_id, branch=branch,
                    )
                # 同步刷新 elapsed
                resp["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                resp["input_tokens"] = input_tokens
                # 与 chat 链路对齐：chat 总是返回单数 memory_id；coding 双层 writer
                # 返回复数 experience_ids（一次 add 通常 0~1 条 ADD）。补一个单数
                # memory_id 取首个 experience_id，让调用方无需区分 scene 即可读
                # resp["memory_id"]。
                _coding_eids = resp.get("experience_ids") or []
                resp["memory_id"] = _coding_eids[0] if _coding_eids else None
                return resp

            # 判定为 chat：strip tool 消息再继续
            if has_tool:
                logger.info(
                    "[add] classified as CHAT; stripping tools"
                )
                chat_messages = strip_tool_messages(chat_messages)

        if content_type == "messages":
            preview = f"messages({len(chat_messages)} turns)"
            logger.info(
                f"[add] writing {preview} user={uid} "
                f"agent_id={agent_id} session_id={session_id} mode={self._mode}"
            )
            logger.info(f"[add] messages detail: {[m.to_dict() for m in chat_messages]}")
        else:
            logger.info(
                f"[add] writing text({len(content)} chars) user={uid} "
                f"agent_id={agent_id} session_id={session_id} mode={self._mode} "
                f"content={content}"
            )

        # mode → agent_mode 映射
        # lite: 不调 LLM agent
        # pro/ultra: 调 LLM agent 做事实提取
        agent_mode = "disabled" if self._mode == "lite" else "full"
        extra = dict(metadata) if metadata else {}
        extra["agent_mode"] = agent_mode
        extra["mode"] = self._mode

        write_req = WriteRequest(
            content=content,
            messages=chat_messages,
            user_id=uid,
            agent_id=agent_id,
            source=source,
            session_id=session_id,
            content_type=content_type,
            extra=extra,
            memory_at=memory_at,
            # 显式透传 request_id（本地变量，不受 contextvar 污染）作为整条写入链路
            # 落库 memory_ops / WRITE_SUMMARY / pipeline_logs 的单一真相源。
            request_id=request_id,
            client_meta=meta,
        )

        # mode → writer:
        #   lite/pro → MemoryWriter (区别在 agent_mode)
        #   ultra    → System2Writer（write 只执行 System 1；System 2 由 evolve() 触发）
        if self._mode == "ultra":
            writer = await self._registry.get_system2_writer()
        else:
            writer = await self._registry.get_writer()
        logger.debug(f"[add] using writer: {writer.VERSION} (mode={self._mode})")
        result: WriteResponse = await writer.write(write_req)

        resp = {
            "success": result.success,
            "scene": "chat",
            "memory_id": result.memory_id,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            "error_code": result.error_code if result.error_code else None,
            "error_message": result.error_message or None,
        }

        # timing breakdown
        _timing = result.extra.get("timing")
        if _timing:
            resp["timing"] = _timing

        # reconcile op counts + stored memory ids (analytics passthrough)
        if result.extra.get("reconcile_ops"):
            resp["ops_summary"] = result.extra["reconcile_ops"]
        if result.extra.get("agent_stored_ids"):
            resp["memory_ids"] = result.extra["agent_stored_ids"]
        _attach_write_diagnostics(resp, result.extra)

        # token usage：纯 input（tiktoken）+ extract / reconcile 两条 LLM 链路的
        # prompt/completion 消耗（lite 模式无 LLM，extract/reconcile 为 0）。
        resp["input_tokens"] = input_tokens
        resp["extract_tokens"] = result.extra.get(
            "extract_tokens", {"prompt": 0, "completion": 0, "total": 0}
        )
        resp["reconcile_tokens"] = result.extra.get(
            "reconcile_tokens", {"prompt": 0, "completion": 0, "total": 0}
        )

        if result.success:
            logger.info(f"[add] done: memory_id={result.memory_id} elapsed={result.elapsed_ms:.0f}ms")

            # 记录历史（非阻塞，失败仅 warning）
            if self._history_store is not None:
                try:
                    isolation_key = self._build_isolation_key(uid, agent_id)
                    # 从 content 或 messages 中提取用于记录的文本
                    history_content = content or "; ".join(
                        f"{m.role}: {m.content}" for m in chat_messages
                    )
                    await self._history_store.record_add(
                        memory_id=result.memory_id,
                        content=history_content,
                        layer=result.layer or "",
                        isolation_key=isolation_key,
                    )
                except Exception as e:
                    logger.warning(f"[add] history record failed: {e}")
        else:
            logger.warning(f"[add] failed: {result.error_message}")

        return resp

    def add_extracted(
        self,
        contents: Union[str, List[str]],
        *,
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        source: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Optional[datetime] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """写入「调用方已抽取好的」记忆：跳过 extract，直接进 reconcile。

        适用场景：调用方（如 openclaw memory_add tool、上游 agent）已经把对话
        抽象成结论性的 memory content，不需要 SDK 再跑一遍 LLM extract。SDK 仍会
        执行完整 reconcile（与已有记忆判重 / 合并 / 矛盾演化），并落 L2_FACT。

        与 add() 的区别：
        - 不调 LLM extractor（省一次 LLM 调用、更快、更省 token）。
        - 不写 L1_RAW 原始节点（content 本身即事实）。
        - contents 每条视为一条独立事实，多条会一起进 reconcile 互相判重。

        Args:
            contents: 单条事实字符串，或多条事实字符串列表。
            其余参数语义同 add()。

        Returns:
            {"success", "memory_id", "memory_ids", "request_id", "elapsed_ms",
             "ops_summary", ...}
        """
        return self._loop_thread.run(
            self.async_add_extracted(
                contents, user_id=user_id, agent_id=agent_id,
                session_id=session_id, source=source, metadata=metadata,
                memory_at=memory_at, request_id=request_id,
            ),
            include_runtime_diag=True,
        )

    @_ensure_internal_loop
    async def async_add_extracted(
        self,
        contents: Union[str, List[str]],
        *,
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        source: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Optional[datetime] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """写入已抽取好的记忆（异步）；跳过 extract，直接 reconcile。详见 add_extracted()。"""
        request_id = request_id or str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()
        from .utils.source_filter import normalize_source
        source = normalize_source(source)

        if isinstance(contents, str):
            content_list = [contents]
        else:
            content_list = list(contents or [])
        content_list = [c.strip() for c in content_list if isinstance(c, str) and c.strip()]

        if not content_list:
            return {
                "success": False,
                "request_id": request_id,
                "error_code": 400,
                "error_message": "contents is required (str or non-empty List[str])",
            }

        from .utils.token_count import count_input_tokens
        input_tokens = sum(count_input_tokens(c, None) for c in content_list)

        extra = dict(metadata) if metadata else {}
        extra["extracted_contents"] = content_list
        extra["agent_mode"] = "extracted"
        extra["mode"] = self._mode

        write_req = WriteRequest(
            content=content_list[0],
            user_id=user_id,
            agent_id=agent_id,
            source=source,
            session_id=session_id,
            content_type="text",
            extra=extra,
            memory_at=memory_at,
            request_id=request_id,
        )

        # lite/pro → MemoryWriter；ultra → System2Writer（内部复用 MemoryWriter）
        if self._mode == "ultra":
            writer = await self._registry.get_system2_writer()
        else:
            writer = await self._registry.get_writer()
        result: WriteResponse = await writer.write_extracted(write_req)

        resp = {
            "success": result.success,
            "scene": "chat",
            "memory_id": result.memory_id,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            "error_code": result.error_code if result.error_code else None,
            "error_message": result.error_message or None,
        }
        if result.extra.get("reconcile_ops"):
            resp["ops_summary"] = result.extra["reconcile_ops"]
        if result.extra.get("agent_stored_ids"):
            resp["memory_ids"] = result.extra["agent_stored_ids"]
        _attach_write_diagnostics(resp, result.extra)
        resp["input_tokens"] = input_tokens
        resp["reconcile_tokens"] = result.extra.get(
            "reconcile_tokens", {"prompt": 0, "completion": 0, "total": 0}
        )

        if result.success:
            logger.info(
                f"[add_extracted] done: {len(result.extra.get('agent_stored_ids', []))} "
                f"nodes from {len(content_list)} contents, elapsed={resp['elapsed_ms']:.0f}ms"
            )
        else:
            logger.warning(f"[add_extracted] failed: {result.error_message}")

        return resp

    def _spawn_search_dedup(
        self,
        all_mems: List[Dict[str, Any]],
        *,
        request_id: str,
        user_id: str,
        agent_id: str,
    ) -> None:
        """对 search 结果中的 L2_FACT + L4_IDENTITY 做去重（fire-and-forget）。

        只取这两层（重复重灾区）；带 evolution_chain 的项视为链头，其 chain node_ids
        从 evolution_chain 取出供连带处理。整体后台执行，不阻塞 search 返回。

        判重按 owner 分批（group_items_by_owner）：search 默认不传 agent_ids，
        结果里可能混着多个 agent（甚至多个 user）的节点 —— owner=user 的记忆属于
        用户这一侧，跨 agent 合并判重；owner=agent 的按 agent 隔离。

        命中的重复项置 SHADOW（逻辑删除、可恢复），不是硬删。
        """
        if os.getenv("MEMORY_SEARCH_DEDUP_ENABLED", "true").strip().lower() in {
            "0", "false", "no", "off",
        }:
            return
        import asyncio
        from .pipelines._retrieval.dedup import (
            DedupItem, execute_dedup, group_items_by_owner,
        )

        _DEDUP_LAYERS = {"l2_fact", "l4_identity"}
        targets = [
            m for m in all_mems
            if (m.get("layer") or "").lower() in _DEDUP_LAYERS
            and m.get("memory_id")
            and m.get("_memory_kind") != "canonical_state"
        ]
        if len(targets) < 2:
            return

        vector_store = self._vector_store
        cache = getattr(self, "_cache", None)

        async def _run():
            try:
                import time as _time
                node_ids = [m["memory_id"] for m in targets]
                _t_emb = _time.perf_counter()
                embs = await vector_store.get_embeddings(node_ids)
                _get_emb_ms = round((_time.perf_counter() - _t_emb) * 1000, 2)
                items: List[DedupItem] = []
                for m in targets:
                    mid = m["memory_id"]
                    emb = embs.get(mid)
                    if not emb:
                        continue
                    chain = m.get("evolution_chain") or []
                    chain_ids = [c.get("node_id") for c in chain if c.get("node_id")] if chain else [mid]
                    items.append(DedupItem(
                        node_id=mid,
                        embedding=emb,
                        content=m.get("content", ""),
                        is_latest=True,
                        is_chain_head=bool(chain),
                        gmt_created=(float(m["gmt_created"]) if m.get("gmt_created") else None),
                        chain_node_ids=chain_ids or [mid],
                        # 分批维度：search 默认不传 agent_ids，_all_mems 里可能混着
                        # 多个 agent（甚至多个 user）的节点。owner=user 的跨 agent
                        # 合并判重，owner=agent 的按 agent 隔离。
                        owner=m.get("owner"),
                        user_id=m.get("user_id", "") or user_id,
                        agent_id=m.get("agent_id", "") or agent_id,
                        source=m.get("source", "default") or "default",
                    ))
                if len(items) >= 2:
                    for _batch in group_items_by_owner(
                        items,
                        default_user_id=user_id,
                        default_agent_id=agent_id,
                    ):
                        await execute_dedup(
                            _batch["items"],
                            vector_store=vector_store,
                            cache=cache,
                            trigger="search",
                            request_id=request_id,
                            # 此前固定传 effective_*_ids[0]，跨 user/跨 agent
                            # 检索时 DEDUP log 的归属是错的（记第一个，删的可能
                            # 是另一个的数据）。分批后每批归属确定。
                            user_id=_batch["user_id"],
                            agent_id=_batch["agent_id"],
                            delete_from_store=True,
                            extra_parsed={
                                "embedding_source": "vdb_get_embeddings",
                                "get_embeddings_ms": _get_emb_ms,
                                "embed_count": len(node_ids),
                                "embed_ok_count": len(items),
                                "owner_bucket": _batch["owner"],
                                "source_bucket": _batch["source"],
                                "batch_size": len(_batch["items"]),
                            },
                        )
            except Exception as e:
                logger.debug(f"[search] background dedup failed: {e}")

        try:
            # dedup 是非用户可感知的后台任务，且只碰 VDB（线程池，无 loop 亲和）。
            # 显式派发到 write loop，避免占用 search loop 的调度权、让下一次 search 排队。
            asyncio.run_coroutine_threadsafe(_run(), self._loop_thread._loop)
        except RuntimeError:
            # 无事件循环（极少见）：同步跳过，不影响主流程
            logger.debug("[search] no running loop for dedup; skipped")

    @_ensure_search_loop
    async def async_search(
        self,
        query: Optional[str] = None,
        *,
        queries: Optional[List[str]] = None,
        scene: Optional[str] = None,
        search_mode: str = "default",
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        limit: int = 10,
        min_score: float = 0.4,
        working_fact_min_score: Optional[float] = None,
        working_experience_min_score: Optional[float] = None,
        schema_min_score: float = 0.4,
        schema_limit: int = 10,
        intention_limit: int = 0,
        created_after: Optional[float] = None,
        reader: str = "",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        layers: Optional[List[Any]] = None,
        embed_override: Optional[Dict[str, Any]] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """搜索记忆（异步）。详见 search() docstring。

        embed_override: 本次搜索 query embedding 的端点覆盖（不改 model/dims，只换接入点）。
            dict，可含 base_url / api_key / extra_headers / timeout；None = 用 client 配置。
            须在 @_ensure_search_loop 委托进 search loop **之后**再 set contextvar——
            contextvar 不跨 run_coroutine_threadsafe 边界，故在此(已在 search loop 上)包裹。
        """
        if embed_override:
            with embed_endpoint_override(
                base_url=embed_override.get("base_url"),
                api_key=embed_override.get("api_key"),
                extra_headers=embed_override.get("extra_headers"),
                timeout=embed_override.get("timeout"),
            ):
                return await self._async_search_impl(
                    query, queries=queries, scene=scene, search_mode=search_mode,
                    user_ids=user_ids,
                    agent_ids=agent_ids, session_ids=session_ids, limit=limit,
                    include_sources=include_sources, exclude_sources=exclude_sources,
                    min_score=min_score,
                    working_fact_min_score=working_fact_min_score,
                    working_experience_min_score=working_experience_min_score,
                    schema_min_score=schema_min_score, schema_limit=schema_limit,
                    intention_limit=intention_limit, created_after=created_after,
                    reader=reader, workspace_id=workspace_id, branch=branch,
                    request_id=request_id, layers=layers,
                    query_embedding=query_embedding,
                )
        return await self._async_search_impl(
            query, queries=queries, scene=scene, search_mode=search_mode,
            user_ids=user_ids,
            agent_ids=agent_ids, session_ids=session_ids, limit=limit,
            include_sources=include_sources, exclude_sources=exclude_sources,
            min_score=min_score,
            working_fact_min_score=working_fact_min_score,
            working_experience_min_score=working_experience_min_score,
            schema_min_score=schema_min_score, schema_limit=schema_limit,
            intention_limit=intention_limit, created_after=created_after,
            reader=reader, workspace_id=workspace_id, branch=branch,
            request_id=request_id, layers=layers,
            query_embedding=query_embedding,
        )

    async def _async_search_impl(
        self,
        query: Optional[str] = None,
        *,
        queries: Optional[List[str]] = None,
        scene: Optional[str] = None,
        search_mode: str = "default",
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        limit: int = 10,
        min_score: float = 0.4,
        working_fact_min_score: Optional[float] = None,
        working_experience_min_score: Optional[float] = None,
        schema_min_score: float = 0.4,
        schema_limit: int = 10,
        intention_limit: int = 0,
        created_after: Optional[float] = None,
        reader: str = "",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        request_id: Optional[str] = None,
        layers: Optional[List[Any]] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """搜索记忆（异步）。详见 search() docstring。

        layers（默认空）：只召回指定层级，接受字符串或 MemoryLayer。
        过滤在召回阶段生效（而非召回后再筛），避免无关层级挤占 limit 名额。
        """
        # ── 入参归一化 ──
        if queries is not None and query is not None:
            raise ValueError("query and queries are mutually exclusive; choose one")
        if queries is None and query is None:
            raise ValueError("either query or queries is required")
        queries_supplied = queries is not None
        if queries is None:
            queries_list: List[str] = [query]
        else:
            if not isinstance(queries, list) or not queries:
                raise ValueError("queries must be a non-empty list[str]")
            queries_list = [str(q) for q in queries]
        target_query = queries_list[-1]

        search_mode_norm = str(search_mode or "default").strip().lower()
        if search_mode_norm not in ("default", "agentic"):
            raise ValueError(
                f"search_mode must be 'default' or 'agentic', got {search_mode!r}"
            )

        # scene 校验 + 归一化
        # 对外统一用 "chat" / "working" / "coding"（与 add() 一致）；内部检索链路仍用
        # "normal" / "productivity"，这里在入参边界做一次映射。
        #   "working"      → "productivity"（走 coding 召回，通用生产力变体）
        #   "coding"       → "productivity"（走 coding 召回，软件工程变体：额外挂
        #                     版本演进链块，见 _scene_variant / _decorate_with_chain）
        #   "chat"         → "normal"（走 chat 召回）
        #   "productivity" / "normal" 作为旧别名保留，向后兼容。
        scene_norm: Optional[str] = None
        if scene is not None:
            sn = str(scene).strip().lower()
            _scene_map = {
                "working": "productivity",
                "coding": "productivity",
                "chat": "normal",
                "productivity": "productivity",  # 向后兼容
                "normal": "normal",              # 向后兼容
            }
            if sn not in _scene_map:
                raise ValueError(
                    f"scene must be 'chat', 'working' or 'coding', got {scene!r}"
                )
            scene_norm = _scene_map[sn]

        # 生成 / 复用 request_id 并注入日志上下文（App 可传入以对齐 SEARCH_API_ACCEPTED）
        request_id = (request_id or "").strip() or str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        # 运行时停顿归因基线：与 t0 同点采样，终值在 log_scene_judge 前取，差值落进
        # READ_SCENE_JUDGE.parsed。用于解释"纯同步代码却耗几百 ms"——区分
        # cgroup CPU throttling（throttle_ms≈elapsed_ms）/ GC 长停顿（gc_delta>=1）/
        # GIL 争抢（两者都≈0 但仍慢）。二者均为同步求值，与 elapsed_ms 同等可信。
        _stall_gc0 = _gc_collection_count()
        _stall_thr0 = _read_throttled_usec()

        effective_user_ids = user_ids if user_ids is not None else []
        effective_agent_ids = agent_ids if agent_ids is not None else []
        effective_session_ids = session_ids if session_ids is not None else []
        lite_mode = getattr(self, "_mode", "pro") == "lite"
        from .utils.source_filter import normalize_source_filters
        effective_include_sources, effective_exclude_sources = normalize_source_filters(
            include_sources, exclude_sources,
        )

        # 校验: 必须传 user_ids
        if not effective_user_ids:
            raise ValueError("user_ids is required and cannot be empty")

        # 校验: 多 agent_ids + 多 session_ids 非法
        if len(effective_agent_ids) > 1 and len(effective_session_ids) > 1:
            raise ValueError(
                "Cannot specify multiple agent_ids and multiple session_ids simultaneously. "
                "Use single agent_id with multiple session_ids, or multiple agent_ids without session_ids."
            )

        # ────────────────────────────────────────────────────────────
        # Search mode + scene 路由
        #
        #   scene 显式指定                         → 直接路由，零 LLM
        #   default + query="..."                 → 固定 normal，零 LLM
        #   default + queries=[...]                → 保持原多-query judge/rewrite 逻辑
        #   agentic + query="..." / queries=[...] → 三分类 + rewrite + 时间
        #   default 多-query 输入超长              → 跳 rewrite；需要时仅做分类
        #
        # queries= 本身就是用户对上下文 judge/rewrite 的显式选择；agentic 则进一步
        # 让单 query 也走同样能力。query_rewrite_max_chars 作为共同长度保护。
        # ────────────────────────────────────────────────────────────
        coding_enabled = (
            getattr(self._config, "coding", None) is not None
            and self._config.coding.enable
        )
        agentic_enabled = search_mode_norm == "agentic"
        rewrite_enabled = bool(
            getattr(self._config.llm, "enable_query_rewrite", False)
        )
        _max_chars = int(getattr(self._config.llm, "query_rewrite_max_chars", 1000) or 1000)
        _combined_len = sum(len(q) for q in queries_list)
        _too_long = _combined_len > _max_chars

        effective_target_query = target_query  # 默认用 queries[-1] 或 query
        rewrite_created_after: Optional[float] = None  # rewrite 抽出的时间下界
        decided_scene: str
        decided_variant: str = HyMemoryClient._scene_variant(scene)
        rewrite_used: bool = False
        scene_mode: str = "default"          # explicit / llm / default
        scene_is_coding: bool = False
        _t_scene = t0
        # scene judge 真实的 LLM 净耗时。只有走 LLM 分支（scene_mode="llm"）才有值；
        # mode=default/explicit 是纯同步分支，保持 None → 不落 parsed.llm_ms。
        # 与「本环节整体耗时」（elapsed_ms → parsed.step_ms）严格区分。
        _scene_llm_ms: Optional[float] = None

        # 当前系统时间（供 rewrite 换算相对时间）
        from datetime import datetime as _dt_now
        _current_time_iso = _dt_now.now().isoformat(timespec="seconds")

        def _ensure_llm():
            if self._coding_llm_provider is None:
                from .agent.llm_provider import LLMProvider
                self._coding_llm_provider = LLMProvider(self._config)
            return self._coding_llm_provider

        if lite_mode:
            # lite 是严格的 raw-only 向量检索模式。scene / search_mode 不得把它
            # 路由到 working/coding，也不应触发任何 LLM judge/rewrite。
            decided_scene = "normal"
            scene_mode = "lite"
            scene_is_coding = False
            decided_variant = "working"
            logger.info(
                "[search] mode=lite forces raw-only normal search "
                "(ignore scene/search_mode/layers; no LLM)"
            )
        elif scene_norm is not None:
            # 1) 显式指定 → 不调 LLM
            decided_scene = scene_norm
            scene_mode = "explicit"
            scene_is_coding = (decided_scene == "productivity")
            decided_variant = HyMemoryClient._scene_variant(scene)
            logger.info(
                f"[search] scene explicit={decided_scene} (no LLM judge); "
                f"target={effective_target_query!r}"
            )
        elif not agentic_enabled and not queries_supplied:
            # 默认 light search 仅指 query="..."：固定 normal，绝不调用 LLM。
            decided_scene = "normal"
            logger.info(
                f"[search] search_mode=default single-query scene=normal (no LLM); "
                f"target={target_query!r}"
            )
        elif agentic_enabled:
            # agentic：单/多 query 都强制一次 LLM 完成三分类 + rewrite + 时间解析。
            # judge 内部已有有界 query 视图，因此原始 queries 再长也不降级成 classify-only。
            want_classify = True
            _t_llm = time.perf_counter()
            try:
                from .coding.judge import analyze_queries
                ar = await analyze_queries(
                    queries_list, _ensure_llm(),
                    want_classify=want_classify, want_rewrite=True,
                    current_time=_current_time_iso,
                )
            except Exception as e:
                logger.warning(f"[search] analyze_queries failed, fallback normal: {e}")
                ar = {"is_coding": False, "rewrite_query": target_query,
                      "created_after": None, "ok": False}
            # 失败也计时：失败往往正是超时，耗时本身是关键信息
            _scene_llm_ms = (time.perf_counter() - _t_llm) * 1000

            scene_mode = "llm"
            if ar.get("ok"):
                judged_variant = str(ar.get("scene") or "chat") if want_classify else "chat"
                scene_is_coding = judged_variant in ("working", "coding")
                if scene_is_coding:
                    decided_variant = judged_variant
                decided_scene = "productivity" if scene_is_coding else "normal"
                new_q = ar.get("rewrite_query") or target_query
                if new_q and new_q != target_query:
                    effective_target_query = new_q
                    rewrite_used = True
                rewrite_created_after = ar.get("created_after")
                logger.info(
                    f"[search] agentic scene={decided_scene} via rewrite-LLM "
                    f"(classify={want_classify}); orig={target_query!r} "
                    f"effective={effective_target_query!r} rewrite_used={rewrite_used} "
                    f"created_after={rewrite_created_after}"
                )
            else:
                decided_scene = "normal"
                logger.info(
                    f"[search] agentic rewrite-LLM fallback=normal; target={target_query!r}"
                )
        elif rewrite_enabled and not _too_long:
            # default + queries= 的原逻辑：配置开启时一次 LLM 做 rewrite/time，
            # working memory 开启时同时三分类。
            want_classify = coding_enabled
            _t_llm = time.perf_counter()
            try:
                from .coding.judge import analyze_queries
                ar = await analyze_queries(
                    queries_list, _ensure_llm(),
                    want_classify=want_classify, want_rewrite=True,
                    current_time=_current_time_iso,
                )
            except Exception as e:
                logger.warning(f"[search] multi-query analyze failed, fallback normal: {e}")
                ar = {"scene": "chat", "is_coding": False,
                      "rewrite_query": target_query, "created_after": None, "ok": False}
            _scene_llm_ms = (time.perf_counter() - _t_llm) * 1000
            scene_mode = "llm"
            if ar.get("ok"):
                judged_variant = str(ar.get("scene") or "chat") if want_classify else "chat"
                scene_is_coding = judged_variant in ("working", "coding")
                if scene_is_coding:
                    decided_variant = judged_variant
                decided_scene = "productivity" if scene_is_coding else "normal"
                new_q = ar.get("rewrite_query") or target_query
                if new_q and new_q != target_query:
                    effective_target_query = new_q
                    rewrite_used = True
                rewrite_created_after = ar.get("created_after")
            else:
                decided_scene = "normal"
        elif coding_enabled and not _too_long:
            # default + queries= 的原逻辑：working memory 开启时判类 + 指代改写。
            _t_llm = time.perf_counter()
            try:
                from .coding.judge import classify_and_rewrite_queries
                jr = await classify_and_rewrite_queries(queries_list, _ensure_llm())
            except Exception as e:
                logger.warning(f"[search] multi-query classify/rewrite failed: {e}")
                jr = {"scene": "chat", "is_coding": False,
                      "rewrite_query": target_query, "ok": False}
            _scene_llm_ms = (time.perf_counter() - _t_llm) * 1000
            scene_mode = "llm"
            if jr.get("ok"):
                judged_variant = str(jr.get("scene") or "chat")
                scene_is_coding = judged_variant in ("working", "coding")
                if scene_is_coding:
                    decided_variant = judged_variant
                decided_scene = "productivity" if scene_is_coding else "normal"
                new_q = jr.get("rewrite_query") or target_query
                if new_q and new_q != target_query:
                    effective_target_query = new_q
                    rewrite_used = True
            else:
                decided_scene = "normal"
        elif coding_enabled:
            # default + 超长 queries：保留旧行为，只分类、不改写。
            _t_llm = time.perf_counter()
            try:
                from .coding.judge import classify_queries_scene
                judged_variant = await classify_queries_scene(
                    queries_list, _ensure_llm()
                )
                scene_is_coding = judged_variant in ("working", "coding")
                if scene_is_coding:
                    decided_variant = judged_variant
            except Exception as e:
                logger.warning(f"[search] multi-query classify failed: {e}")
                scene_is_coding = False
            _scene_llm_ms = (time.perf_counter() - _t_llm) * 1000
            scene_mode = "llm"
            decided_scene = "productivity" if scene_is_coding else "normal"
        else:
            # default + queries=，但两个相关能力都关闭。
            decided_scene = "normal"
            logger.info(
                f"[search] default multi-query features disabled; no LLM; "
                f"target={target_query!r}"
            )

        # ────────────────────────────────────────────────────────────
        # 场景判定落 pipeline log（search 的第一步，在 reader 之前）
        # ────────────────────────────────────────────────────────────
        if getattr(self, "_cache", None) is not None:
            # 停顿归因终值：必须在构造 ReadTraceLogger / 调 log_scene_judge 之前取，
            # 与 elapsed_ms 的 perf_counter 口径圈定同一段代码（t0 → 此处）。
            _stall_gc_delta = _gc_collection_count() - _stall_gc0
            _stall_throttle_ms = (_read_throttled_usec() - _stall_thr0) / 1000.0
            try:
                from .pipelines._retrieval.trace import ReadTraceLogger
                _scene_trace = ReadTraceLogger(
                    cache=self._cache,
                    request_id=request_id,
                    user_id=(effective_user_ids[0] if effective_user_ids else ""),
                    agent_id=(effective_agent_ids[0] if effective_agent_ids else "default_agent"),
                    reader_version="client",
                    session_id=(effective_session_ids[0] if len(effective_session_ids) == 1 else ""),
                )
                await _scene_trace.log_scene_judge(
                    query=target_query or "",
                    scene=decided_scene,
                    mode=scene_mode,
                    is_coding=scene_is_coding,
                    rewrite_query=(effective_target_query if rewrite_used else None),
                    rewrite_used=rewrite_used,
                    created_after=(
                        datetime.fromtimestamp(rewrite_created_after).strftime("%Y-%m-%d %H:%M:%S")
                        if rewrite_created_after is not None else None
                    ),
                    elapsed_ms=(time.perf_counter() - _t_scene) * 1000 if _t_scene else 0.0,
                    llm_ms=_scene_llm_ms,
                    gc_delta=_stall_gc_delta,
                    throttle_ms=_stall_throttle_ms,
                )
            except Exception as _e:
                logger.debug(f"[search] scene judge trace failed: {_e}")

        # ────────────────────────────────────────────────────────────
        # 按决定的 scene 分流
        # ────────────────────────────────────────────────────────────
        if decided_scene == "productivity":
            if not coding_enabled:
                logger.warning(
                    "[search] scene=productivity requested but productivity link disabled; "
                    "falling back to normal"
                )
                decided_scene = "normal"
            else:
                logger.info(
                    f"[search] routing to PRODUCTIVITY path variant={decided_variant}: "
                    f"target={effective_target_query!r} "
                    f"workspace_id={workspace_id!r} branch={branch!r}"
                )
                return await self._search_coding(
                    effective_target_query,
                    user_ids=effective_user_ids,
                    agent_ids=effective_agent_ids,
                    session_ids=effective_session_ids,
                    workspace_id=workspace_id,
                    branch=branch,
                    limit=limit,
                    working_fact_min_score=working_fact_min_score,
                    working_experience_min_score=working_experience_min_score,
                    request_id=request_id,
                    t0=t0,
                    # 走类而不是 self：_scene_variant 是 staticmethod，而 async_search
                    # 会被测试绑到 SimpleNamespace 上单独调用（见
                    # tests/coding/test_search_scene_routing.py），self 上没有这个属性。
                    variant=decided_variant,
                    include_sources=effective_include_sources,
                    exclude_sources=effective_exclude_sources,
                )

        # ────────────────────────────────────────────────────────────
        # Chat 召回链路（原有逻辑）
        # ────────────────────────────────────────────────────────────
        logger.info(
            f"[search] query='{effective_target_query}' user_ids={effective_user_ids} "
            f"agent_ids={effective_agent_ids} session_ids={effective_session_ids} limit={limit}"
        )

        # rewrite 抽出的时间下界与用户显式 created_after 取更晚者（更严格）
        effective_created_after = created_after
        if rewrite_created_after is not None:
            effective_created_after = (
                rewrite_created_after if effective_created_after is None
                else max(effective_created_after, rewrite_created_after)
            )

        # lite 是纯向量存取：写入只产生 ACTIVE 的 L1_RAW，不会再抽取 L2/L4。
        # 这是 mode 级硬约束，不是默认值：即使调用方显式传了其他 layers，也必须
        # 忽略并只查 L1_RAW。pro / ultra 仍沿用原有层级语义。
        from .models.memory import MemoryLayer

        effective_search_layers = [MemoryLayer.L1_RAW] if lite_mode else layers
        effective_schema_limit = 0 if lite_mode else schema_limit
        effective_intention_limit = 0 if lite_mode else intention_limit

        read_req = ReadRequest(
            query=effective_target_query,
            user_ids=effective_user_ids,
            agent_ids=effective_agent_ids,
            session_ids=effective_session_ids,
            include_sources=effective_include_sources,
            exclude_sources=effective_exclude_sources,
            limit=limit,
            min_score=min_score,
            schema_min_score=schema_min_score,
            schema_limit=effective_schema_limit,
            intention_limit=effective_intention_limit,
            request_id=request_id,
            created_after=effective_created_after,
            # ReadRequest.layers 是 List[str]；reader 侧再转回枚举。
            # 此处仍走 _normalize_layers，以便非法值在入口就报错、并统一旧编号别名。
            layers=(
                [
                    layer.value
                    for layer in self._normalize_layers(effective_search_layers)
                ]
                if effective_search_layers else None
            ),
            query_embedding=query_embedding,
        )

        reader_impl = await self._registry.get_reader(reader_name=reader)
        logger.debug(f"[search] using reader: {reader_impl.VERSION}")
        result: ReadResponse = await reader_impl.read(read_req)

        if not result.success:
            logger.warning(f"[search] failed: {result.error_message}")
            return {
                "request_id": request_id,
                "memories": {"schema": [], "proactive": [], "normal": []},
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        # layers 兜底过滤：定向召回由各 reader 实现，但 reader 的支持程度不一
        # （profile / graph 路常按固定层级召回，不吃 request.layers），只靠 reader
        # 会漏出未被请求的层级。契约是「只返回命中层级」，故在此再筛一次。
        _wanted_layers = (
            {
                layer.value
                for layer in self._normalize_layers(effective_search_layers)
            }
            if effective_search_layers else None
        )

        _all_mems = []
        for mem in result.memories:
            if _wanted_layers is not None and mem.get("layer", "") not in _wanted_layers:
                continue
            if lite_mode and mem.get("layer", "") != MemoryLayer.L1_RAW.value:
                continue
            score = mem.get("score", 0.0)
            item = {
                "memory_id": mem.get("memory_id", ""),
                "content": mem.get("content", ""),
                "score": round(score, 4),
                "layer": mem.get("layer", ""),
                # 多 user 检索时调用方需据此追溯归属（R1）
                "user_id": mem.get("user_id", "") or "",
                "agent_id": mem.get("agent_id", "") or "",
                "source": mem.get("source", "default") or "default",
                "retrieval_source": mem.get("retrieval_source", "") or "",
                "owner": mem.get("owner"),
                "speculate": mem.get("speculate"),
                # 不透出 source_raw_memory_id：它指向 L1_RAW 节点，而 raw 对外
                # 读路径一律不可见，该 id 到手也查不到东西（按 ID 取返回"不存在"），
                # 留着只会让调用方以为能据此追溯原文。
                # 节点上仍保留该字段，内部溯源与 clone 重映射不受影响。
                "tags": mem.get("tags") or [],
                "memory_at": mem.get("memory_at"),
                "gmt_created": mem.get("gmt_created"),
                # 调用方自定义 meta（透传负载）。由 reader 从 custom["meta"] 取出，
                # 未实现该字段的 reader 下为空列表。
                "meta": mem.get("meta") or [],
                "_schema_channel": bool(mem.get("_schema_channel")),
            }
            if mem.get("schema_type"):
                item["schema_type"] = mem["schema_type"]
            if mem.get("workspace_id"):
                item["workspace_id"] = mem["workspace_id"]
            if mem.get("branch"):
                item["branch"] = mem["branch"]
            if mem.get("evolution_chain"):
                item["evolution_chain"] = mem["evolution_chain"]
            if mem.get("capsule_memories") is not None:
                item["capsule_memories"] = mem["capsule_memories"]
            if mem.get("episode_id"):
                item["episode_id"] = mem["episode_id"]
            if mem.get("member_ids") is not None:
                item["member_ids"] = mem["member_ids"]
            if mem.get("timeline") is not None:
                item["timeline"] = mem["timeline"]
            if mem.get("rerank_score") is not None:
                item["rerank_score"] = mem["rerank_score"]
            _all_mems.append(item)

        _dedup_mems = []
        for source, public in zip(result.memories, _all_mems):
            copy = dict(public)
            copy["_memory_kind"] = source.get("_memory_kind", "")
            _dedup_mems.append(copy)

        # search 链路去重：仅对 L2_FACT + L4_IDENTITY（重复重灾区）判重删库。
        # L0 及其他层不参与。fire-and-forget，不阻塞本次返回（清理面向后续查询）。
        try:
            self._spawn_search_dedup(
                _dedup_mems,
                request_id=request_id,
                user_id=(effective_user_ids[0] if effective_user_ids else ""),
                agent_id=(effective_agent_ids[0] if effective_agent_ids else ""),
            )
        except Exception as e:
            logger.debug(f"[search] spawn dedup failed (non-fatal): {e}")

        # Memory Strength：搜索后 best-effort 回写 access_count+1 / last_accessed_at=now
        # （fire-and-forget，不阻塞响应；失败静默）。用 reader 透出的 access_count 计算 +1，
        # 避免再读一次。受总开关 strength_enabled 约束（默认关闭），并可用
        # MEMORY_RECALL_ACCESS_TRACKING=false 单独关闭回写。
        _recall_cfg = self._config.recall
        if (getattr(_recall_cfg, "strength_enabled", False)
                and getattr(_recall_cfg, "access_tracking_enabled", True)):
            _bump_items = [
                (mem.get("memory_id", ""), mem.get("access_count", 0))
                for mem in result.memories
                if mem.get("memory_id")
            ]
            if _bump_items:
                from .pipelines._retrieval.strength import bump_access as _bump_access

                async def _do_bump():
                    try:
                        await _bump_access(self._vector_store, _bump_items)
                    except Exception as _be:
                        logger.debug(f"[search] access bump failed: {_be}")

                try:
                    # access-bump 只碰 VDB，且不是关键路径 → 派发到 write loop，
                    # 不占用 search loop 调度权。
                    asyncio.run_coroutine_threadsafe(_do_bump(), self._loop_thread._loop)
                except Exception as _be:
                    logger.debug(f"[search] access bump schedule failed: {_be}")

        # 按 channel 分组：schema 只包含 L6；L0 由 get_user_profile() 单独返回。
        # proactive = intention；其余（含 L2/L3/L4）归 normal。
        _SCHEMA_LAYERS = {"l6_schema"}
        _PROACTIVE_LAYERS = {"l7_intention"}
        memories = {"schema": [], "proactive": [], "normal": []}
        total = 0
        for mem in _all_mems:
            channel = _search_memory_channel(
                mem,
                schema_layers=_SCHEMA_LAYERS,
                proactive_layers=_PROACTIVE_LAYERS,
                schema_min_score=schema_min_score,
            )
            memories[channel].append(mem)
            total += 1

        logger.info(
            f"[search] done: {total} results "
            f"(schema={len(memories['schema'])} proactive={len(memories['proactive'])} "
            f"normal={len(memories['normal'])}), elapsed={result.elapsed_ms:.0f}ms"
        )
        for ch, mems in memories.items():
            for i, mem in enumerate(mems):
                logger.debug(
                    f"[search] {ch}#{i} score={mem['score']:.4f} "
                    f"[{mem['layer']}] {mem['content']}"
                )

        # 记录搜索历史（可选，非阻塞）
        if self._history_store is not None and self._config.history.record_searches:
            try:
                hist_user = effective_user_ids[0] if effective_user_ids else "default"
                hist_agent = effective_agent_ids[0] if effective_agent_ids else "default"
                isolation_key = self._build_isolation_key(hist_user, hist_agent)
                await self._history_store.record_search(
                    query=effective_target_query,
                    isolation_key=isolation_key,
                    results_count=total,
                )
            except Exception as e:
                logger.warning(f"[search] history record failed: {e}")

        output = {
            "request_id": request_id,
            "memories": memories,
            "rewrite": {
                "used": rewrite_used,
                "original_query": target_query,
                "effective_query": effective_target_query,
                "created_after": rewrite_created_after,
                "scene": decided_scene,
            },
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        for key in (
            "episode_capsule",
            "episode_member_expansion",
            "retrieval_clauses",
            "local_rerank",
            "hierarchy_bundle",
            "multi_seed_bundle",
            "supplemental_propositions",
            "dynamic_entity_tag_embedding",
            "tag_index_nominee",
        ):
            if key in result.extra:
                output[key] = result.extra[key]
        return output

    # ================================================================
    # CRUD API（异步）— get / update / delete / delete_all
    # ================================================================

    @_ensure_internal_loop
    async def async_get(
        self,
        memory_id: str,
        *,
        include_raw: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        获取单条记忆（异步）。

        VDB + Graph 双路并行查询，哪个有结果返回哪个。

        Args:
            memory_id: 记忆 ID
            include_raw: 是否允许返回 L1_RAW 原始记忆，默认 False。

        Returns:
            {"memory_id", "content", "layer", "source", "request_id", "elapsed_ms"} 或 None
        """
        import asyncio as _asyncio_get
        from .models.memory import MemoryLayer

        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        logger.info(f"[get] memory_id={memory_id}")

        # 双路并行
        async def _get_vdb():
            return await self._vector_store.get_by_id(memory_id)

        async def _get_graph():
            if self._graph_store is None:
                return None
            try:
                return await self._graph_store.get_node(memory_id)
            except Exception as e:
                logger.debug(f"[get] graph_store query failed: {e}")
                return None

        vdb_node, graph_node = await _asyncio_get.gather(_get_vdb(), _get_graph())

        # L1_RAW 默认不对外可见：按 ID 直取走主键查询、绕过向量库的 layer
        # filter，故在此统一挡掉。SDK 调用方可通过 include_raw=True 显式读取；
        # HTTP 接口不传该参数，继续保持隐藏。内部写链路不经过此方法。
        #
        # 置于 gather 之后、埋点之前：命中 raw 仍照常落 GET 埋点（保留可观测性，
        # 便于发现有调用方拿 raw id 试探），只是不把内容返回给调用方。
        # 对调用方的表现与"该 id 不存在"完全一致，不额外暴露"存在但被挡"的信息。
        if (
            not include_raw
            and vdb_node is not None
            and vdb_node.layer == MemoryLayer.L1_RAW
        ):
            vdb_node = None
        if (
            not include_raw
            and graph_node is not None
            and getattr(graph_node, "layer", None) == MemoryLayer.L1_RAW
        ):
            graph_node = None

        elapsed = round((time.perf_counter() - t0) * 1000, 2)

        # pipeline trace（best-effort，受 MEMORY_PIPELINE_TRACE_ENABLED 控制）
        _hit_node = vdb_node if vdb_node is not None else graph_node
        _source = "vdb" if vdb_node is not None else ("graph" if graph_node is not None else "")
        _ident = self._backend_identity()
        _parsed = {
            "memory_id": memory_id,
            **_ident,
            "hit": _hit_node is not None,
            "source": _source,
        }
        if _hit_node is not None:
            try:
                _parsed["layer"] = _hit_node.layer.value
            except Exception:
                pass
        await self._maybe_store_op_log(
            step="GET",
            request_id=request_id,
            user_id=(_hit_node.user_id if _hit_node else "") or "",
            agent_id=(getattr(_hit_node, "agent_id", "") if _hit_node else "") or "",
            parsed=_parsed,
            memory_ids=[memory_id],
            elapsed_ms=elapsed,
        )

        # VDB 优先（如果两边都有，以 VDB 为准）
        if vdb_node is not None:
            return {
                "memory_id": vdb_node.node_id,
                "content": vdb_node.content,
                "layer": vdb_node.layer.value,
                "status": vdb_node.status.value if vdb_node.status else "active",
                "is_latest": vdb_node.is_latest,
                "supersedes": vdb_node.supersedes or [],
                "superseded_by": vdb_node.superseded_by or [],
                "tags": vdb_node.tags or [],
                "memory_at": int(vdb_node.memory_at.timestamp()) if isinstance(vdb_node.memory_at, datetime) else None,
                "gmt_created": int(vdb_node.gmt_created.timestamp()) if isinstance(vdb_node.gmt_created, datetime) else None,
                "speculate": getattr(vdb_node, "speculate", None),
                # 不透出 source_raw_memory_id：见 async_search 同处说明。
                "user_id": vdb_node.user_id,
                "agent_id": vdb_node.agent_id,
                "confidence": vdb_node.confidence,
                "custom": vdb_node.custom or {},
                "meta": list((vdb_node.custom or {}).get("meta") or []),
                "source": "vdb",
                "request_id": request_id,
                "elapsed_ms": elapsed,
            }

        if graph_node is not None:
            return {
                "memory_id": graph_node.node_id,
                "content": graph_node.content,
                "layer": graph_node.layer.value if graph_node.layer else "",
                "status": graph_node.status.value if graph_node.status else "active",
                "is_latest": getattr(graph_node, "is_latest", True),
                "supersedes": graph_node.supersedes or [],
                "superseded_by": graph_node.superseded_by or [],
                "tags": graph_node.tags or [],
                "memory_at": int(graph_node.memory_at.timestamp()) if isinstance(graph_node.memory_at, datetime) else None,
                "gmt_created": int(graph_node.gmt_created.timestamp()) if isinstance(graph_node.gmt_created, datetime) else None,
                "speculate": getattr(graph_node, "speculate", None),
                # 不透出 source_raw_memory_id：见 async_search 同处说明。
                "user_id": graph_node.user_id,
                "agent_id": graph_node.agent_id,
                "confidence": graph_node.confidence,
                "custom": getattr(graph_node, "custom", {}) or {},
                "source": "graph",
                "request_id": request_id,
                "elapsed_ms": elapsed,
            }

        logger.info(f"[get] not found: memory_id={memory_id}")
        return None

    @_ensure_internal_loop
    async def async_update(self, memory_id: str, content: str) -> Dict[str, Any]:
        """
        更新记忆内容（异步）。

        流程: get_by_id → 404 校验 → embed → 更新节点字段 → upsert → 记录历史

        Returns:
            {"success", "memory_id", "request_id", "elapsed_ms"}
        """
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        logger.info(f"[update] memory_id={memory_id} new_content({len(content)} chars)")

        # 获取旧节点
        node = await self._vector_store.get_by_id(memory_id)
        if node is None:
            logger.warning(f"[update] not found: memory_id={memory_id}")
            return {
                "success": False,
                "memory_id": memory_id,
                "request_id": request_id,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "error_message": f"memory_id '{memory_id}' not found",
            }

        old_content = node.content
        old_status = node.status.value

        # 生成新 embedding
        new_embedding = await self._embed_service.embed(content)

        # 更新节点
        node.content = content
        node.embedding = new_embedding

        await self._vector_store.upsert(node)
        try:
            from .pipelines._retrieval.retrieval_clauses import (
                sync_retrieval_clause_index,
            )
            await sync_retrieval_clause_index(
                vector_store=self._vector_store,
                embed_service=self._embed_service,
                nodes=[node],
                enabled=bool(getattr(
                    self._config.recall, "retrieval_clauses_enabled", False
                )),
                min_words=int(getattr(
                    self._config.recall, "retrieval_clauses_min_words", 70
                )),
                clear_empty=True,
            )
        except Exception as e:
            logger.warning("[retrieval-clause] client.update maintain failed: %s", e)
        logger.info(f"[update] done: memory_id={memory_id}")

        # 记录历史
        if self._history_store is not None:
            try:
                uid = node.user_id or ""
                agent_id = node.agent_id or "default"
                isolation_key = self._build_isolation_key(uid, agent_id)
                await self._history_store.record_update(
                    memory_id=memory_id,
                    old_content=old_content,
                    new_content=content,
                    old_status=old_status,
                    new_status=node.status.value,
                    change_reason="client.update",
                    isolation_key=isolation_key,
                )
            except Exception as e:
                logger.warning(f"[update] history record failed: {e}")

        return {
            "success": True,
            "memory_id": memory_id,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def _backend_identity(self) -> Dict[str, str]:
        """解析当前 VDB 后端身份：provider / db_name / collection_name。

        供 delete/get/list 等操作落 pipeline trace 用。没有数据库命名空间概念的后端
        会返回空的 db_name。
        """
        vs = self._vector_store
        provider = ""
        try:
            provider = str(getattr(self._config.vector_store, "provider", "") or "")
        except Exception:
            provider = ""
        return {
            "provider": provider,
            "db_name": str(getattr(vs, "_db_name", "") or ""),
            "collection_name": str(getattr(vs, "_collection_name", "") or ""),
        }

    async def _maybe_store_op_log(
        self,
        *,
        step: str,
        request_id: str,
        user_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        parsed: Optional[Dict[str, Any]] = None,
        memory_ids: Optional[List[str]] = None,
        elapsed_ms: float = 0.0,
        event_at_ms: int = 0,
    ) -> None:
        """为 delete/get/list 等非 LLM 操作落一条 pipeline trace（best-effort）。

        复用 pipeline_logs_v2 表：prompt/response 留空、token 为 0，结构化信息全部
        放进 parsed(JSON)。受 MEMORY_PIPELINE_TRACE_ENABLED 控制，与 search 一致。
        失败静默降级，绝不影响主操作。

        event_at_ms: 事件真实发生时刻（epoch 毫秒）。绝大多数调用方是直接
            `await _maybe_store_op_log(...)`，observability wrapper 首行的
            `time.time()` 与调用点只差几微秒，无需传此参数（传 0 即可）。
            **只有经 `create_task` / `ensure_future` 派发的调用方必须显式传**——
            派发会让 wrapper 晚若干毫秒才执行，届时它自己打的时间戳已经偏晚，
            导致 pipeline log 的 timestamp 与真实时序倒挂。
        """
        if self._cache is None:
            return
        try:
            from .utils.pipeline_observability import is_pipeline_trace_enabled
            if not is_pipeline_trace_enabled():
                return
            await self._cache.store_pipeline_log(
                request_id=request_id,
                user_id=user_id or "",
                agent_id=agent_id or "",
                session_id=session_id or "",
                step=step,
                prompt="",
                response="",
                parsed=json.dumps(parsed or {}, ensure_ascii=False),
                memory_ids=memory_ids or None,
                elapsed_ms=float(elapsed_ms or 0.0),
                event_at_ms=int(event_at_ms or 0),
            )
        except Exception as e:
            logger.debug(f"[{step.lower()}] store op trace failed (non-fatal): {e}")

    @_ensure_internal_loop
    async def async_delete(self, memory_id: str) -> Dict[str, Any]:
        """
        删除单条记忆（异步）。

        同时删除向量库和图数据库（如有）中的对应节点。

        演化链处理：
        - 如果删除的是链头（is_latest=True + supersedes 非空），
          将前驱节点恢复为 is_latest=True + status=ACTIVE
        - 如果删除的是链中间节点，将其 supersedes 指向的前驱重新挂到后继的 supersedes 上

        Graph evidence 处理：
        - 删除 Graph 中该节点 + 关联的 VdbRef 影子节点 + DERIVED_FROM 边

        Returns:
            {"success", "deleted_count", "request_id", "elapsed_ms"}
        """
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        logger.info(f"[delete] memory_id={memory_id}")

        # 先获取节点信息（用于链修复 + history + tag 清理）
        old_content = ""
        old_tags: List[str] = []
        old_user_id = ""
        old_isolation_key = ""
        node = await self._vector_store.get_by_id(memory_id)
        if node is not None:
            old_content = node.content
            try:
                old_tags = list(node.tags or [])
            except Exception:
                old_tags = []
            old_user_id = node.user_id or ""
            try:
                old_isolation_key = node.get_isolation_key() or ""
            except Exception:
                old_isolation_key = ""

        # ── 演化链修复（VDB 删除前执行）──
        chain_repaired = False
        if node is not None:
            await self._repair_evolution_chain_on_delete(node)
            chain_repaired = bool(node.supersedes or node.superseded_by)

        # 删除向量库
        deleted = await self._vector_store.delete(memory_id)
        try:
            await self._vector_store.delete_retrieval_clauses(memory_id)
        except Exception as e:
            logger.debug("[retrieval-clause] delete cleanup failed: %s", e)

        # 删除图数据库节点 + 清理 evidence（如有）
        graph_deleted = False
        if self._graph_store is not None:
            try:
                await self._graph_store.delete_node(memory_id)
                graph_deleted = True
            except Exception as e:
                logger.warning(f"[delete] graph_store delete failed: {e}")
            # 清理该节点作为 VdbRef 被其他 Schema 引用的情况
            try:
                await self._cleanup_graph_evidence(memory_id)
            except Exception as e:
                logger.debug(f"[delete] graph evidence cleanup failed (non-fatal): {e}")

        deleted_count = 1 if deleted else 0

        # 清理 tag_index：此 memory 删除后，检查其每个 tag 是否还有其他 memory 引用；
        # 无引用则从 tag_index 中移除。失败静默降级，不影响 delete 语义。
        if deleted and old_tags and old_user_id:
            try:
                from .pipelines._retrieval import tag_index as _tag_index_helper
                removed = await _tag_index_helper.cleanup_tags_on_delete(
                    vector_store=self._vector_store,
                    user_id=old_user_id,
                    tags=old_tags,
                    isolation_key=old_isolation_key,
                )
                if removed:
                    logger.debug(f"[delete] tag_index cleaned {removed} orphan tags")
            except Exception as e:
                logger.debug(f"[delete] tag_index cleanup failed (non-fatal): {e}")

        # 记录历史
        if self._history_store is not None and deleted:
            try:
                uid = (node.user_id if node else "") or ""
                agent_id = (node.agent_id if node else "") or "default"
                isolation_key = self._build_isolation_key(uid, agent_id)
                await self._history_store.record_delete(
                    memory_id=memory_id,
                    content=old_content,
                    isolation_key=isolation_key,
                )
            except Exception as e:
                logger.warning(f"[delete] history record failed: {e}")

        logger.info(f"[delete] done: deleted_count={deleted_count}")

        # pipeline trace（best-effort，受 MEMORY_PIPELINE_TRACE_ENABLED 控制）
        _ident = self._backend_identity()
        _parsed = {
            "memory_id": memory_id,
            **_ident,
            "found": node is not None,
            "vdb_deleted": bool(deleted),
            "graph_deleted": graph_deleted,
            "deleted_count": deleted_count,
            "chain_repaired": chain_repaired,
        }
        if node is not None:
            _parsed["user_id"] = node.user_id or ""
            _parsed["agent_id"] = node.agent_id or ""
        await self._maybe_store_op_log(
            step="DELETE",
            request_id=request_id,
            user_id=(node.user_id if node else "") or "",
            agent_id=(node.agent_id if node else "") or "",
            parsed=_parsed,
            memory_ids=[memory_id],
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

        return {
            "success": deleted,
            "deleted_count": deleted_count,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    async def _acquire_graph_store_for_purge(self):
        """
        获取用于 purge 的 GraphStore（与 mode 无关）。

        ultra 模式复用常驻连接；pro/lite 在检测到遗留图数据时临时打开并 purge 后关闭。
        Returns:
            (graph_store | None, ephemeral: bool) — ephemeral=True 时调用方须 close。
        """
        if self._graph_store is not None:
            return self._graph_store, False

        from .data.graph_store import create_graph_store, graph_data_may_exist

        if not graph_data_may_exist(self._config):
            return None, False

        gs = create_graph_store(self._config)
        await gs.initialize()
        if getattr(gs, "_available", True) is False:
            logger.warning(
                "[purge] ephemeral graph store unavailable (e.g. Kuzu file lock); "
                "skip graph purge"
            )
            await gs.close()
            return None, False

        provider = getattr(self._config.graph_store, "provider", None) or "kuzu"
        logger.info(
            f"[purge] opened ephemeral GraphStore for delete_all "
            f"(mode={self._mode}, provider={provider})"
        )
        return gs, True

    async def _purge_graph_data(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> tuple:
        """按 user 范围清理图数据（VDB 与 mode 无关，图库亦同）。

        Returns:
            (deleted_count: int, elapsed_ms: float, ephemeral: bool)
            供调用方落 DELETE_ALL_GRAPH pipeline log，区分
            "VDB 慢" 与 "Graph 临时建连慢（ephemeral=True 时 initialize() 耗时）"。
        """
        _t = time.perf_counter()
        gs, ephemeral = await self._acquire_graph_store_for_purge()
        if gs is None:
            return 0, round((time.perf_counter() - _t) * 1000, 2), False
        try:
            n = await gs.delete_by_metadata(
                user_id=user_id, agent_id=agent_id, session_id=session_id,
            )
            elapsed_ms = round((time.perf_counter() - _t) * 1000, 2)
            logger.info(
                f"[delete_all] graph purged: user={user_id} agent={agent_id} "
                f"session={session_id} memory_nodes={n} ephemeral={ephemeral}"
            )
            return n, elapsed_ms, ephemeral
        except Exception as e:
            logger.warning(f"[delete_all] graph_store purge failed: {e}")
            return 0, round((time.perf_counter() - _t) * 1000, 2), ephemeral
        finally:
            if ephemeral:
                await gs.close()

    @_ensure_internal_loop
    async def async_delete_all(
        self,
        *,
        user_id: str = "",
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        删除用户的记忆（异步）。

        默认删除该用户的所有记忆。可通过 agent_ids/session_ids 缩小范围。

        - agent_ids=[], session_ids=[] → 删除 user 下所有 agent、所有 session
        - agent_ids=["a1"] → 删除 user 下 a1 的所有 session
        - agent_ids=["a1"], session_ids=["s1","s2"] → 删除指定组合

        Returns:
            {"success", "deleted_count", "request_id", "elapsed_ms"}
        """
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        uid = user_id
        effective_agent_ids = agent_ids if agent_ids is not None else []
        effective_session_ids = session_ids if session_ids is not None else []

        total_deleted = 0
        total_graph_deleted = 0
        # 分段耗时聚合（供 DELETE_ALL_SUMMARY 落库）
        vdb_ms_total = 0.0
        graph_ms_total = 0.0
        rounds = 0   # 实际执行了几轮(agent×session 组合数)

        async def _do_round(
            aid: Optional[str] = None,
            sid: Optional[str] = None,
        ) -> None:
            """一个 (agent, session) 组合的 VDB + Graph 删除，各落一条 pipeline log。"""
            nonlocal total_deleted, total_graph_deleted, vdb_ms_total, graph_ms_total, rounds
            rounds += 1

            # ── VDB 删除 ──────────────────────────────────────────────
            _t_vdb = time.perf_counter()
            try:
                cnt = await self._vector_store.delete_by_metadata(
                    user_id=uid,
                    agent_id=aid,
                    session_id=sid,
                )
            except Exception as e:
                logger.warning(f"[delete_all] vdb delete failed aid={aid} sid={sid}: {e}")
                cnt = -1
            try:
                await self._vector_store.delete_retrieval_clauses_by_metadata(
                    user_id=uid,
                    agent_id=aid,
                    session_id=sid,
                )
            except Exception as e:
                logger.debug(
                    "[retrieval-clause] delete_all cleanup failed "
                    "aid=%s sid=%s: %s", aid, sid, e,
                )
            _vdb_ms = round((time.perf_counter() - _t_vdb) * 1000, 2)
            if cnt and cnt > 0:
                total_deleted += cnt
            vdb_ms_total += _vdb_ms

            await self._maybe_store_op_log(
                step="DELETE_ALL_VDB",
                request_id=request_id,
                user_id=uid,
                agent_id=aid or "",
                parsed={
                    "agent_id": aid,
                    "session_id": sid,
                    "deleted_count": cnt if cnt and cnt > 0 else 0,
                    "vdb_ms": _vdb_ms,
                },
                elapsed_ms=_vdb_ms,
            )

            # ── Graph 删除 ────────────────────────────────────────────
            g_cnt, _graph_ms, ephemeral = await self._purge_graph_data(
                uid, agent_id=aid, session_id=sid,
            )
            total_graph_deleted += g_cnt
            graph_ms_total += _graph_ms

            await self._maybe_store_op_log(
                step="DELETE_ALL_GRAPH",
                request_id=request_id,
                user_id=uid,
                agent_id=aid or "",
                parsed={
                    "agent_id": aid,
                    "session_id": sid,
                    "graph_deleted_count": g_cnt,
                    "graph_ms": _graph_ms,
                    # ephemeral=True 时含临时 initialize() 开销，是 Graph 超时的常见原因
                    "ephemeral": ephemeral,
                },
                elapsed_ms=_graph_ms,
            )

        # ── 路由到具体的删除模式 ──────────────────────────────────────
        if not effective_agent_ids and not effective_session_ids:
            logger.info(f"[delete_all] deleting all for user={uid}")
            await _do_round()

        elif effective_agent_ids and not effective_session_ids:
            for aid in effective_agent_ids:
                logger.info(f"[delete_all] deleting user={uid} agent={aid}")
                await _do_round(aid=aid)

        else:
            a_ids = effective_agent_ids or ["default_agent"]
            for aid in a_ids:
                for sid in effective_session_ids:
                    logger.info(f"[delete_all] deleting user={uid} agent={aid} session={sid}")
                    await _do_round(aid=aid, sid=sid)

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[delete_all] done: vdb_deleted={total_deleted} "
            f"graph_memory_nodes={total_graph_deleted} mode={self._mode}"
        )

        # ── 总览 log ──────────────────────────────────────────────────
        await self._maybe_store_op_log(
            step="DELETE_ALL_SUMMARY",
            request_id=request_id,
            user_id=uid,
            parsed={
                "total_deleted": total_deleted,
                "graph_deleted": total_graph_deleted,
                "rounds": rounds,
                "vdb_ms_total": round(vdb_ms_total, 2),
                "graph_ms_total": round(graph_ms_total, 2),
                # scope：方便 inspector 一眼看出删的是什么范围
                "scope": (
                    "all" if not effective_agent_ids and not effective_session_ids
                    else f"agents={effective_agent_ids}" if not effective_session_ids
                    else f"agents={effective_agent_ids} sessions={effective_session_ids}"
                ),
            },
            elapsed_ms=elapsed_ms,
        )

        return {
            "success": True,
            "deleted_count": total_deleted,
            "graph_deleted_count": total_graph_deleted,
            "request_id": request_id,
            "elapsed_ms": elapsed_ms,
        }

    # ================================================================
    # List Memories API（异步）
    # ================================================================

    @staticmethod
    def _memory_node_to_list_item(node) -> Dict[str, Any]:
        """将 MemoryNode 序列化为 list API 条目。"""
        _memory_at = None
        if isinstance(node.memory_at, datetime):
            _memory_at = int(node.memory_at.timestamp())
        _gmt_created = None
        if isinstance(node.gmt_created, datetime):
            _gmt_created = int(node.gmt_created.timestamp())
        if _gmt_created is None and isinstance(getattr(node, "valid_from", None), datetime):
            _gmt_created = int(node.valid_from.timestamp())

        return {
            "memory_id": node.node_id,
            "content": node.content,
            "layer": node.layer.value if node.layer else "",
            "status": node.status.value if node.status else "active",
            "memory_at": _memory_at,
            "gmt_created": _gmt_created,
            "user_id": node.user_id,
            "agent_id": node.agent_id,
            "session_id": getattr(node, "session_id", None),
            "speculate": getattr(node, "speculate", None),
            # 不透出 source_raw_memory_id：见 async_search 同处说明。
            "tags": node.tags or [],
            "custom": node.custom or {},
            # 调用方自定义 meta（透传负载）。与 search / get 一致地放在顶层，
            # 免得调用方为不同接口写两套取值逻辑；custom 内含内部字段，
            # 上层不应直接透出给业务方。
            "meta": list((node.custom or {}).get("meta") or []),
        }

    @staticmethod
    def _normalize_layers(layers: Optional[List[Any]]) -> Optional[List]:
        """把 str / MemoryLayer 混合的 layers 规范化为去重后的 MemoryLayer 列表。

        接受字符串是为了与 search 侧对齐（ReadRequest.layers 为 List[str]），
        并复用 from_string 的旧编号兼容（如 l5_schema → l6_schema）。
        非法值直接抛 ValueError，由调用方（App 层）转成 400，不静默忽略——
        静默忽略会让调用方以为筛选生效，实际拿到的是全量。
        """
        from .models.memory import MemoryLayer

        if not layers:
            return None
        out: List[MemoryLayer] = []
        for item in layers:
            layer = item if isinstance(item, MemoryLayer) else MemoryLayer.from_string(str(item))
            if layer not in out:
                out.append(layer)
        return out

    @staticmethod
    def _sort_memory_nodes(nodes: List, *, order: str) -> List:
        reverse = (order.lower() != "asc")

        def _sort_key(n):
            val = n.memory_at if n.memory_at is not None else n.gmt_created
            if val is None:
                val = getattr(n, "valid_from", None)
            if val is None:
                return datetime.min
            if isinstance(val, datetime):
                return val
            return datetime.min

        nodes.sort(key=_sort_key, reverse=reverse)
        return nodes

    async def _list_vdb_bucket(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        limit: int,
        offset: int,
        order: str,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List] = None,
        with_evolution: bool = False,
    ) -> Dict[str, Any]:
        from .models.memory import MemoryLayer, MemoryStatus
        from .pipelines._retrieval.user_level_l0 import collapse_user_level_l0_hits

        # 过滤条件维持改动前原样（仅 status=ACTIVE），本次只加多值枚举。
        # 不叠加 only_latest：正常数据下 writer 对 is_latest/status 成对写，两者等效，
        # 加了只能多拦脏数据，却会改变现网 list 的返回口径。
        # 列表页只需 payload（content/tags/custom/链头信息）与排序字段，
        # 不涉及向量运算，一概 with_vectors=False，避免拉回大量 embedding。
        #
        # L1_RAW 的排除由向量库侧完成（allow_raw_layer 默认 False，条件随请求
        # 下发，raw 不出库）。此前是把 raw 一并捞回进程再用 Python 筛掉，
        # 既让原始对话经过本进程，又白拉一遍最大的那一层，已移除。
        all_nodes = await self._vector_store.list_by_user(
            user_id=user_id,
            agent_id=agent_id,
            user_ids=user_ids,
            agent_ids=agent_ids,
            status_filter=[MemoryStatus.ACTIVE],
            layers=layers,
            with_vectors=False,
        )

        # L0 是 user-level，不应被 agent 过滤。默认列表或显式包含 L0 时，额外按
        # user 查询 L0，并把历史多个 agent-level 链头折叠为每个用户一条。
        # L0 已无 agent 隔离，缺少 user 范围时必须跳过，避免返回其他用户画像。
        include_l0 = layers is None or MemoryLayer.L0_BASIC_INFO in layers
        if include_l0 and (user_id or user_ids):
            all_nodes = [n for n in all_nodes if n.layer != MemoryLayer.L0_BASIC_INFO]
            l0_nodes = await self._vector_store.list_by_user(
                user_id=user_id,
                user_ids=user_ids,
                status_filter=[MemoryStatus.ACTIVE],
                layers=[MemoryLayer.L0_BASIC_INFO],
                with_vectors=False,
            )
            l0_hits = collapse_user_level_l0_hits([
                {"node_id": n.node_id, "node": n, "score": 1.0}
                for n in l0_nodes
            ])
            all_nodes.extend(hit["node"] for hit in l0_hits if hit.get("node") is not None)

        all_nodes = self._sort_memory_nodes(all_nodes, order=order)
        total = len(all_nodes)
        page_nodes = all_nodes[offset: offset + limit]
        items = [self._memory_node_to_list_item(n) for n in page_nodes]
        if with_evolution:
            await self._attach_evolution_chains(page_nodes, items)
        return {
            "memories": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def _attach_evolution_chains(self, nodes: List, items: List[Dict[str, Any]]) -> None:
        """就地给 list 条目附加 evolution_chain，结构与 search 侧一致。

        只展开当前页：回溯要按链逐条查库，整表展开会随数据量线性放大，
        而列表页一次只渲染一页。

        只附加字段、不增删条目。expand_evolution_chains 自带同链去重（search 侧
        可能召回同链多个节点），但 list 已按 status=ACTIVE 过滤，历史版本必为
        SUPERSEDED，同链多条不会出现；即便出现也不能让 list 少返回条目，
        故按链头 node_id 映射回原条目，而非直接采用其输出。
        """
        from .pipelines._retrieval.evolution import expand_evolution_chains

        linked = [
            n for n in nodes
            if getattr(n, "supersedes", None) or getattr(n, "superseded_by", None)
        ]
        if not linked:
            return

        try:
            expanded = await expand_evolution_chains(
                self._vector_store,
                [{"node_id": n.node_id, "score": 0.0, "node": n} for n in linked],
            )
        except Exception as e:
            # 演化链是附加信息，取不到不应让整个列表失败
            logger.warning(f"[list] evolution chain expand failed: {e}")
            return

        chains = {
            exp.get("node_id"): exp["evolution_chain"]
            for exp in expanded
            if isinstance(exp, dict) and exp.get("evolution_chain")
        }
        for item in items:
            chain = chains.get(item.get("memory_id"))
            if chain:
                item["evolution_chain"] = chain

    async def _list_graph_bucket(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        limit: int,
        offset: int,
        order: str,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List] = None,
    ) -> Optional[Dict[str, Any]]:
        """列出图库 L6/L7 节点；无图数据、不可用或 layers 不含图层时返回 None。"""
        from .models.memory import MemoryLayer, MemoryNode

        graph_layers = [MemoryLayer.L6_SCHEMA, MemoryLayer.L7_INTENTION]
        if layers:
            graph_layers = [layer for layer in graph_layers if layer in layers]
            # 指定的 layers 全在 VDB 侧，无需连图库（连接本身有开销）
            if not graph_layers:
                return None

        gs, ephemeral = await self._acquire_graph_store_for_purge()
        if gs is None:
            return None

        # 图库按 isolation_key 组织，无 in 查询能力，多值只能按组合逐个拉后合并
        effective_uids = list(user_ids) if user_ids else ([user_id] if user_id else [])
        effective_aids = list(agent_ids) if agent_ids else [agent_id or "default_agent"]
        iks = [
            MemoryNode.build_isolation_key(u, a)
            for u in effective_uids
            for a in effective_aids
        ]

        try:
            async def _fetch(ik: str, layer) -> List:
                try:
                    return await gs.get_all_nodes(ik, layer=layer, limit=10_000)
                except Exception as e:
                    logger.warning(
                        f"[list] graph list ik={ik} layer={layer.value} failed: {e}"
                    )
                    return []

            chunks = await asyncio.gather(*[
                _fetch(ik, layer)
                for ik in iks
                for layer in graph_layers
            ])
            graph_nodes = [n for chunk in chunks for n in chunk]

            graph_nodes = self._sort_memory_nodes(graph_nodes, order=order)
            total = len(graph_nodes)
            page_nodes = graph_nodes[offset: offset + limit]
            out = {
                "nodes": [self._memory_node_to_list_item(n) for n in page_nodes],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
            # 单组合时保持原字段形态，避免既有调用方读 isolation_key 失效
            if len(iks) == 1:
                out["isolation_key"] = iks[0]
            else:
                out["isolation_keys"] = iks
            return out
        finally:
            if ephemeral:
                await gs.close()

    async def _list_coding(
        self,
        *,
        user_id: str,
        workspace_id: str,
        branch: Optional[str],
        limit: int,
        offset: int,
        request_id: str,
        t0: float,
    ) -> Dict[str, Any]:
        """按 workspace_id（+可选 branch）纯过滤列出经验层完整详情。

        枚举而非召回，所以只有经验层、不带事实层。无 query、无语义排序，按
        updated_at 倒序。fail-safe：失败返回空列表。
        """
        empty = {
            "request_id": request_id,
            "scene": "productivity",
            "experiences": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        try:
            await self._get_coding_writer()  # 副作用：lazy init store
        except Exception as e:
            logger.warning(f"[list-coding] init failed: {e}")
            return empty

        try:
            mems = await self._coding_store.list_by_workspace(
                user_id=user_id, workspace_id=workspace_id, branch=branch,
                limit=limit, offset=offset,
            )
        except Exception as e:
            logger.warning(f"[list-coding] list_by_workspace failed: {e}")
            return empty

        out_memories = [
            {
                "experience_id": d.experience_id,
                "agent_id": getattr(d, "agent_id", "") or "",
                "task": d.task,
                "search_keys": list(d.search_keys),
                "solution": d.solution,
                "boundary_envs": d.boundary_envs,
                "workspace_id": d.workspace_id,
                "branch": d.branch,
                "files": list(d.files),
                "source_fact_ids": list(d.source_fact_ids),
                "confidence": d.confidence,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                "source": d.source,
            }
            for d in mems
        ]
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[list-coding] user={user_id} workspace_id={workspace_id!r} "
            f"branch={branch!r} returned={len(out_memories)} elapsed={elapsed}ms"
        )
        return {
            "request_id": request_id,
            "scene": "productivity",
            "experiences": out_memories,
            "total": len(out_memories),
            "limit": limit,
            "offset": offset,
            "elapsed_ms": elapsed,
        }

    @_ensure_internal_loop
    async def async_list_memories(
        self,
        *,
        user_id: str = "",
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        user_ids: Optional[List[str]] = None,
        agent_ids: Optional[List[str]] = None,
        layers: Optional[List[Any]] = None,
        with_evolution: bool = False,
    ) -> Dict[str, Any]:
        """
        列出用户的记忆（异步）。

        VDB 与图库分桶返回（图库仅在存在 Kuzu/Neo4j 数据时包含 graph 键）。

        user_ids / agent_ids（多值，默认空）：
            - 提供时优先于单值 user_id / agent_id，一次返回多个用户（或多个 agent）
              的记忆并集，分页在合并排序后统一施加。
            - 每条记忆自带 user_id / agent_id，调用方据此追溯归属。
            - coding 路径（workspace_id 非空）不支持多值，仅取单值 user_id。

        layers（默认空）：
            - 只返回指定层级，接受字符串或 MemoryLayer（与 search 侧写法对齐）；
              非法值抛 ValueError。
            - 不传时行为与改动前一致：返回 L1_RAW 以外的全部层级。
              显式指定则完全以传入为准，包含只要 L1_RAW 的场景。
            - 指定的层级都不在图库（L6/L7）时跳过图库查询，返回中省略 graph 键。

        with_evolution（默认 False）：
            - 为 VDB 条目附加 `evolution_chain`（latest → oldest），结构与 search 一致，
              仅在该条记忆确有演化链时出现。
            - 默认关闭：展开需按链逐条回查，会拖慢默认列表。
            - 只展开当前页，不影响 total 与条目数。
            - 注意能看到的仅限「矛盾取代」型演化；「补充精炼」型在写入时是原地更新，
              历史版本不落库，故无链可返回。

        workspace_id / branch（默认空）：
            - workspace_id 非空 → 直接走 coding memory 列表路径，按 workspace_id
              （+可选 branch）纯过滤列出该 user 的完整经验层（按 updated_at
              倒序，无语义排序），返回 {"scene": "productivity", "experiences": [...]}。
            - 两者都为空 → 常规 chat 路径（VDB + graph 分桶）。

        Returns:
            chat 路径:
            {
                "vdb": {"memories": [...], "total", "limit", "offset"},
                "graph": {"nodes": [...], "total", ...} | 省略,
                "elapsed_ms": ...,
            }
            coding 路径:
            {
                "scene": "productivity",
                "experiences": [...],
                "total": ...,
                "limit": ..., "offset": ...,
                "elapsed_ms": ...,
            }
        """
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        # workspace_id 非空 → 走 coding memory 列表路径
        if workspace_id:
            return await self._list_coding(
                user_id=user_id, workspace_id=workspace_id, branch=branch,
                limit=limit, offset=offset, request_id=request_id, t0=t0,
            )

        effective_layers = self._normalize_layers(layers)

        vdb = await self._list_vdb_bucket(
            user_id=user_id, agent_id=agent_id,
            user_ids=user_ids, agent_ids=agent_ids,
            limit=limit, offset=offset, order=order,
            layers=effective_layers, with_evolution=with_evolution,
        )
        graph = await self._list_graph_bucket(
            user_id=user_id, agent_id=agent_id,
            user_ids=user_ids, agent_ids=agent_ids,
            limit=limit, offset=offset, order=order,
            layers=effective_layers,
        )

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[list] user={user_ids or user_id} agent={agent_ids or agent_id} "
            f"vdb_total={vdb['total']} graph_total={graph['total'] if graph else 'n/a'} "
            f"elapsed={elapsed}ms"
        )

        # pipeline trace（best-effort，受 MEMORY_PIPELINE_TRACE_ENABLED 控制）
        _page_items = vdb.get("memories", []) or []
        _page_ids = [
            (m.get("memory_id") or m.get("id") or "")
            for m in _page_items
            if isinstance(m, dict)
        ]
        _page_ids = [i for i in _page_ids if i]
        _ident = self._backend_identity()
        _parsed = {
            **_ident,
            "user_id": user_id,
            "agent_id": agent_id or "",
            "limit": limit,
            "offset": offset,
            "order": order,
            "vdb_total": vdb.get("total", 0),
            "returned_count": len(_page_items),
        }
        # op log 的 user_id/agent_id 字段是单值，多值查询范围额外记在 parsed 里，避免审计漏记
        if user_ids:
            _parsed["user_ids"] = list(user_ids)
        if agent_ids:
            _parsed["agent_ids"] = list(agent_ids)
        if effective_layers:
            _parsed["layers"] = [layer.value for layer in effective_layers]
        if with_evolution:
            _parsed["with_evolution"] = True
        if graph is not None:
            _parsed["graph_total"] = graph.get("total", 0)
        await self._maybe_store_op_log(
            step="LIST",
            request_id=request_id,
            user_id=user_id or (user_ids[0] if user_ids else ""),
            agent_id=agent_id or (agent_ids[0] if agent_ids else ""),
            parsed=_parsed,
            memory_ids=_page_ids or None,
            elapsed_ms=elapsed,
        )

        out: Dict[str, Any] = {"vdb": vdb, "elapsed_ms": elapsed, "request_id": request_id}
        if graph is not None:
            out["graph"] = graph
        return out

    # ================================================================
    # User Profile / Basic Info（L0）API
    # ================================================================

    @_ensure_internal_loop
    async def async_get_user_profile(
        self,
        *,
        user_id: str,
    ) -> Dict[str, Any]:
        """Return one complete user profile, including enabled Ultra artifacts."""
        from .agent.tools.basic_profile import (
            _assemble_full_kv_from_nodes,
            _latest_l0_heads_from_nodes,
            _render_content,
        )
        from .models.memory import MemoryLayer
        from .pipelines.balanced_catalog import (
            attach_balanced_catalog_to_l0,
            get_active_balanced_catalog,
        )
        from .pipelines.specific_supplement import (
            attach_specific_supplement_to_l0,
            get_active_specific_supplement,
        )

        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("user_id is required and cannot be empty")
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        # Read all L0 versions once: the latest head supplies identity/timestamps,
        # while the complete history is required to reconstruct the full current KV
        # because each L0 version may contain only that write's changed fields.
        l0_nodes = await self._vector_store.list_by_user(
            user_id=normalized_user_id,
            layers=[MemoryLayer.L0_BASIC_INFO],
            limit=1000,
            with_vectors=False,
        )
        full_kv = _assemble_full_kv_from_nodes(list(l0_nodes))
        heads = _latest_l0_heads_from_nodes(list(l0_nodes))
        head = heads[0] if heads else None
        configured_order = list(self._config.basic_profile.effective_fields().keys())
        field_order = configured_order + sorted(
            key for key in full_kv if key not in configured_order
        )
        base_content = _render_content(
            full_kv,
            field_order=field_order,
        )

        l0_results: List[Dict[str, Any]] = []
        if head is not None:
            l0_results.append({
                "node_id": head.node_id,
                "node": head,
                "content": base_content or head.content,
                "layer": MemoryLayer.L0_BASIC_INFO.value,
                "score": 1.0,
                "source": "user_profile",
            })

        catalog = await get_active_balanced_catalog(
            self._graph_store,
            user_id=normalized_user_id,
        )
        l0_results, catalog_info = attach_balanced_catalog_to_l0(
            l0_results, catalog,
        )
        supplement = await get_active_specific_supplement(
            self._graph_store,
            user_id=normalized_user_id,
        )
        l0_results, supplement_info = attach_specific_supplement_to_l0(
            l0_results, supplement,
        )

        profile: Optional[Dict[str, Any]] = None
        if len(l0_results) == 1:
            item = l0_results[0]
            node = item.get("node")
            content = str(
                item.get("content") or getattr(node, "content", "") or ""
            ).strip()
            for suffix_key in (
                "_balanced_catalog_suffix",
                "_specific_supplement_suffix",
            ):
                suffix = str(item.get(suffix_key) or "").strip()
                if suffix:
                    content = content.rstrip() + ("\n\n" if content else "") + suffix
            memory_at = getattr(node, "memory_at", None) if node else None
            gmt_created = getattr(node, "gmt_created", None) if node else None
            profile = {
                "memory_id": str(item.get("node_id") or ""),
                "content": content,
                "layer": MemoryLayer.L0_BASIC_INFO.value,
                "user_id": normalized_user_id,
                "basic_info": full_kv,
                "tags": list(getattr(node, "tags", None) or []),
                "memory_at": (
                    int(memory_at.timestamp()) if isinstance(memory_at, datetime) else None
                ),
                "gmt_created": (
                    int(gmt_created.timestamp()) if isinstance(gmt_created, datetime) else None
                ),
                "catalog": catalog_info,
                "supplement": supplement_info,
            }

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        await self._maybe_store_op_log(
            step="GET_USER_PROFILE",
            request_id=request_id,
            user_id=normalized_user_id,
            agent_id="",
            parsed={
                **self._backend_identity(),
                "found": profile is not None,
                "basic_info_fields": len(full_kv),
                "catalog_attached": bool(catalog_info.get("attached")),
                "supplement_attached": bool(supplement_info.get("attached")),
            },
            memory_ids=[profile["memory_id"]] if profile else None,
            elapsed_ms=elapsed,
        )
        return {
            "request_id": request_id,
            "profile": profile,
            "elapsed_ms": elapsed,
        }

    @_ensure_internal_loop
    async def async_get_user_basic_info(
        self,
        *,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """返回用户的 L0 basic info 记忆（异步）。

        与 search / list 一致：当记忆存在演化链（supersede/evolution）时，以链头
        （is_latest）为代表返回，并附 `evolution_chain`（latest → oldest）。

        Returns:
            {
                "request_id": str,
                "memories": [
                    {
                        "memory_id", "content", "layer", "status",
                        "memory_at", "gmt_created", "tags",
                        "basic_info_kv": {...},          # L0 结构化 KV（来自 custom）
                        "evolution_chain": [...],        # 仅当有链
                    },
                    ...
                ],
                "elapsed_ms": float,
            }
        """
        from .models.memory import MemoryLayer, MemoryStatus
        from .pipelines._retrieval.evolution import expand_evolution_chains
        from .pipelines._retrieval.user_level_l0 import collapse_user_level_l0_hits

        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        # L0 head 是 ACTIVE 状态；链上更早的版本走 evolution 展开时按需拉取。
        # basic_info 组装只读 custom.basic_info_kv / content / tags 与链上元信息，
        # evolution 展开也不涉及 embedding，就一开始走 with_vectors=False 避免拉向量。
        # L0 Basic Info 是 user-level 全局画像；agent_id 仅保留 API 向后兼容。
        nodes = await self._vector_store.list_by_user(
            user_id=user_id,
            status_filter=[MemoryStatus.ACTIVE],
            layers=[MemoryLayer.L0_BASIC_INFO],
            with_vectors=False,
        )

        hits = collapse_user_level_l0_hits([
            {"node_id": n.node_id, "node": n, "score": 1.0}
            for n in nodes
        ])
        expanded = await expand_evolution_chains(self._vector_store, hits)

        memories: List[Dict[str, Any]] = []
        for exp in expanded:
            node = exp.get("node")
            if node is None:
                continue
            _memory_at = (
                int(node.memory_at.timestamp())
                if isinstance(node.memory_at, datetime) else None
            )
            _gmt_created = (
                int(node.gmt_created.timestamp())
                if isinstance(node.gmt_created, datetime) else None
            )
            item = {
                "memory_id": node.node_id,
                "content": node.content,
                "layer": node.layer.value if node.layer else "",
                "status": node.status.value if node.status else "active",
                "memory_at": _memory_at,
                "gmt_created": _gmt_created,
                "tags": node.tags or [],
                "basic_info_kv": (node.custom or {}).get("basic_info_kv", {}),
            }
            if exp.get("evolution_chain"):
                item["evolution_chain"] = exp["evolution_chain"]
            memories.append(item)

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[basic_info] user={user_id} "
            f"count={len(memories)} elapsed={elapsed}ms"
        )

        _parsed = {
            **self._backend_identity(),
            "user_id": user_id,
            "returned_count": len(memories),
        }
        await self._maybe_store_op_log(
            step="GET_BASIC_INFO",
            request_id=request_id,
            user_id=user_id,
            agent_id="",
            parsed=_parsed,
            memory_ids=[m["memory_id"] for m in memories] or None,
            elapsed_ms=elapsed,
        )

        return {
            "request_id": request_id,
            "memories": memories,
            "elapsed_ms": elapsed,
        }

    # ================================================================
    # Entity Store 构建 / 迁移 API
    # ================================================================

    def build_memory_graph(
        self,
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        limit: int = 10000,
        clear_first: bool = False,
    ) -> Dict[str, Any]:
        """为指定 user 的全部 L2_FACT 回填记忆关系图（同步）。

        用途：图数据是增量积累的，开启 `MEMORY_ENABLE_GRAPH` 之前写入的记忆不在图里。
        这个入口把历史 L2_FACT 投影成图节点 + 建 `TAGGED_WITH` + `semantic_neighbor` 边。

        建得出：`TAGGED_WITH`（Topic）+ `semantic_neighbor` + `TEMPORAL_NEXT` 时序脊
        （后者需 `MEMORY_GRAPH_TEMPORAL_SPINE=true`）。

        ⚠️ 回填**建不出** `co_occurs` 和 LLM 标注的 `causes`/`elaborates`/`contrasts`：
        前者需要"同一次写入批次"这个信息（历史数据已丢失），后者需要重跑 reconcile。
        也就是说回填拿不到信息量最高的那两类边，跨主题的因果关系仍要靠新写入积累。

        投影本身是幂等的（节点/标签/边都走 MERGE），重复跑不会叠数据。但它**不会删旧
        节点** —— 如果投影规则变过（例如 Topic 隔离键从 `user::agent::session` 收窄成
        `user::agent`），旧的 session 粒度 Topic 仍会留在库里与新的并存。这种情况传
        `clear_first=True`，重建前先清掉该用户的 l2_fact 投影与孤儿 Topic
        （**不动** System 2 的 l6_schema）。

        Returns:
            {"success", "memories_scanned", "nodes", "tags", "semantic", "errors",
             "elapsed_ms", "cleared"}
        """
        return self._loop_thread.run(
            self.async_build_memory_graph(
                user_id=user_id, agent_id=agent_id, limit=limit,
                clear_first=clear_first,
            )
        )

    @_ensure_internal_loop
    async def async_build_memory_graph(
        self,
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        limit: int = 10000,
        clear_first: bool = False,
    ) -> Dict[str, Any]:
        """`build_memory_graph` 的异步版本。"""
        import time as _t
        from .models.memory import MemoryLayer, MemoryStatus
        from .pipelines._graph.projector import project_memories_to_graph

        t0 = _t.perf_counter()
        if self._graph_store is None:
            return {
                "success": False,
                "error": "graph store not enabled (set MEMORY_ENABLE_GRAPH=true for pro mode)",
            }

        cleared: Dict[str, int] = {}
        if clear_first:
            # 只清 l2_fact 投影 + 孤儿 Topic；System 2 的 l6_schema 不动
            cleared = await self._graph_store.clear_projection(
                user_id, agent_id, layer=MemoryLayer.L2_FACT.value,
            )
            logger.info(f"[graph] cleared before rebuild: {cleared}")

        vector_store = self._registry._get_shared_vector_store()
        nodes = await vector_store.list_by_user(
            user_id=user_id,
            agent_id=agent_id,
            layers=[MemoryLayer.L2_FACT],
            status_filter=[MemoryStatus.ACTIVE],
            limit=limit,
            with_vectors=True,
        )
        nodes = list(nodes or [])

        # semantic_neighbor 回填：用库里已有的向量算 pairwise 相似度，不调 embedding API
        neighbors_by_node: Dict[str, List[Tuple[str, float]]] = {}
        gcfg = getattr(self._config, "graph_store", None)
        thr = getattr(gcfg, "edge_min_score", 0.55) if gcfg else 0.55
        with_emb = [n for n in nodes if getattr(n, "embedding", None)]
        for i, a in enumerate(with_emb):
            na = sum(x * x for x in a.embedding) ** 0.5
            if na == 0:
                continue
            for b in with_emb[i + 1:]:
                nb_ = sum(x * x for x in b.embedding) ** 0.5
                if nb_ == 0:
                    continue
                sim = sum(x * y for x, y in zip(a.embedding, b.embedding)) / (na * nb_)
                if sim >= thr:
                    neighbors_by_node.setdefault(a.node_id, []).append((b.node_id, sim))

        stats = await project_memories_to_graph(
            graph_store=self._graph_store,
            embed_service=self._registry._get_shared_embed_service(),
            nodes=nodes,
            vector_store=vector_store,
            neighbors_by_node=neighbors_by_node,
            edge_min_score=thr,
            # 回填没有"批次"概念，禁掉 co_occurs（否则会把全量记忆两两相连）
            co_occurs_max_batch=0,
            # 时序脊走 bulk 版：整批就是全部历史，前驱在批内直接排序即可。
            # 若误用增量版，会因为"排除批内节点"而一条边都建不出来。
            temporal_spine_bulk=bool(
                getattr(gcfg, "temporal_spine", False) if gcfg else False
            ),
            hierarchy_topic_write=bool(
                getattr(gcfg, "hierarchy_topic_write", False) if gcfg else False
            ),
        )
        return {
            "success": True,
            "memories_scanned": len(nodes),
            **stats,
            "cleared": cleared,
            "elapsed_ms": round((_t.perf_counter() - t0) * 1000, 2),
        }

    def build_entity_store(
        self,
        user_id: str,
        *,
        agent_id: str = "default_agent",
        rebuild: bool = False,
    ) -> Dict[str, Any]:
        """为指定 user 的全部 L2_FACT（含旧数据）抽取 entity 刷入 entity store（同步）。

        用于给历史 collection 补建 entity store，供写侧与内部迁移工具使用。
        与 mode 无关（pro 也可调）。

        Args:
            user_id:  用户 ID
            agent_id: Agent ID（默认 default_agent）
            rebuild:  True 时先清空该 user 现有 entity 再重建（默认 False，增量合并）

        Returns:
            {"success", "memories_scanned", "entities_indexed", "elapsed_ms"}
        """
        return self._loop_thread.run(
            self.async_build_entity_store(
                user_id=user_id, agent_id=agent_id, rebuild=rebuild,
            )
        )

    @_ensure_internal_loop
    async def async_build_entity_store(
        self,
        user_id: str,
        *,
        agent_id: str = "default_agent",
        rebuild: bool = False,
    ) -> Dict[str, Any]:
        """build_entity_store 的异步实现。"""
        from .models.memory import MemoryLayer, MemoryStatus
        from .pipelines._retrieval.entity_store import index_memory_entities

        t0 = time.perf_counter()
        vs = self._vector_store

        # 扫描该 user 的全部 ACTIVE L2_FACT
        # entity rebuild 只需 node_id/content/user_id/agent_id，下游 index_memory_entities 会对
        # entity 名重新 embed，不依赖原 node 的 embedding——走 with_vectors=False 避免拉向量。
        try:
            nodes = await vs.list_by_user(
                user_id=user_id,
                agent_id=agent_id,
                limit=100000,
                status_filter=[MemoryStatus.ACTIVE],
                layers=[MemoryLayer.L2_FACT],
                with_vectors=False,
            )
        except Exception as e:
            logger.error(f"[build_entity_store] list_by_user failed: {e}")
            return {"success": False, "error": str(e), "memories_scanned": 0, "entities_indexed": 0}

        # rebuild：先剔除这些 memory 在 entity store 里的关联（best-effort）
        if rebuild:
            for n in nodes:
                try:
                    await vs.delete_entities_for_memory(
                        memory_id=n.node_id, user_id=user_id, agent_ids=[agent_id] if agent_id else None,
                    )
                except NotImplementedError:
                    return {
                        "success": False,
                        "error": "entity store not supported by this vector store backend",
                        "memories_scanned": 0, "entities_indexed": 0,
                    }
                except Exception as e:
                    logger.debug(f"[build_entity_store] rebuild cleanup failed for {n.node_id}: {e}")

        indexed = 0
        for n in nodes:
            cnt = await index_memory_entities(
                vector_store=vs,
                embed_service=self._embed_service,
                memory_id=n.node_id,
                content=n.content or "",
                user_id=user_id,
                agent_id=agent_id or "default_agent",
            )
            indexed += cnt

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(
            f"[build_entity_store] user={user_id} agent={agent_id} "
            f"scanned={len(nodes)} entities_indexed={indexed} rebuild={rebuild} elapsed={elapsed}ms"
        )
        return {
            "success": True,
            "memories_scanned": len(nodes),
            "entities_indexed": indexed,
            "elapsed_ms": elapsed,
        }

    # ================================================================
    # Clone User API（异步）
    # ================================================================

    async def _load_clone_source_snapshot(
        self,
        src_user_id: str,
        agent_id: Optional[str],
    ) -> Dict[str, Any]:
        """Read immutable source state once for a concurrent clone fan-out."""
        from .models.memory import MemoryLayer

        reads = [self._vector_store.list_by_user(
            src_user_id, agent_id=agent_id, allow_raw_layer=True,
        )]
        include_user_l0 = bool(agent_id)
        if include_user_l0:
            reads.append(self._vector_store.list_by_user(
                src_user_id, layers=[MemoryLayer.L0_BASIC_INFO],
                allow_raw_layer=True,
            ))

        graph_export = getattr(self._graph_store, "export_clone_snapshot", None)
        if callable(graph_export):
            reads.append(graph_export(src_user_id))
        clause_export = getattr(
            self._vector_store, "export_retrieval_clause_snapshot", None,
        )
        if callable(clause_export):
            reads.append(clause_export(
                src_user_id=src_user_id, agent_id=agent_id,
            ))

        values = await asyncio.gather(*reads)
        src_nodes = list(values[0] or [])
        cursor = 1
        if include_user_l0:
            seen_node_ids = {node.node_id for node in src_nodes}
            src_nodes.extend(
                node for node in (values[cursor] or [])
                if not str(getattr(node, "agent_id", "") or "").strip()
                and node.node_id not in seen_node_ids
            )
            cursor += 1
        graph_snapshot = values[cursor] if callable(graph_export) else None
        if callable(graph_export):
            cursor += 1
        clause_snapshot = values[cursor] if callable(clause_export) else None
        return {
            "nodes": tuple(src_nodes),
            "graph": graph_snapshot,
            "retrieval_clauses": clause_snapshot,
        }

    async def _acquire_clone_source_snapshot(
        self,
        src_user_id: str,
        agent_id: Optional[str],
    ) -> Tuple[Dict[str, Any], Tuple[Any, Any]]:
        """Single-flight a source snapshot across concurrent destinations.

        Entries live only while at least one caller is waiting/using the
        snapshot.  They are removed immediately after the final waiter exits,
        so a later clone observes later source writes rather than stale state.
        """
        lock = getattr(self, "_clone_snapshot_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._clone_snapshot_lock = lock
        entries = getattr(self, "_clone_snapshot_entries", None)
        if entries is None:
            entries = {}
            self._clone_snapshot_entries = entries

        key = (src_user_id, agent_id or "")
        async with lock:
            entry = entries.get(key)
            if entry is None:
                entry = {
                    "task": asyncio.create_task(
                        self._load_clone_source_snapshot(src_user_id, agent_id)
                    ),
                    "waiters": 0,
                }
                entries[key] = entry
            entry["waiters"] += 1
            task = entry["task"]
        try:
            # One cancelled HTTP clone must not cancel the shared source read
            # for its sibling rollout destinations.
            return await asyncio.shield(task), (key, entry)
        except BaseException:
            await self._release_clone_source_snapshot((key, entry))
            raise

    async def _release_clone_source_snapshot(
        self, lease: Tuple[Any, Any],
    ) -> None:
        key, entry = lease
        lock = getattr(self, "_clone_snapshot_lock", None)
        entries = getattr(self, "_clone_snapshot_entries", None)
        if lock is None or entries is None:
            return
        async with lock:
            current = entries.get(key)
            if current is entry:
                current["waiters"] -= 1
                if current["waiters"] <= 0:
                    entries.pop(key, None)

    @_ensure_internal_loop
    async def async_clone_user(
        self,
        src_user_id: str,
        dst_user_id: str,
        *,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        克隆用户记忆（异步）。

        深拷贝 src 的全部记忆到 dst，embedding 直接复制不重新计算。
        dst 必须为空，否则返回错误。操作同步完成，失败时回滚已写入的数据。

        Returns:
            {"success": True, "request_id": "...", "cloned_count": N, "elapsed_ms": ...}
        """
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        t0 = time.perf_counter()

        if not src_user_id or not dst_user_id:
            return {
                "success": False,
                "request_id": request_id,
                "cloned_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "error_message": "src_user_id and dst_user_id are required",
            }

        if src_user_id == dst_user_id:
            return {
                "success": False,
                "request_id": request_id,
                "cloned_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "error_message": "src_user_id and dst_user_id must be different",
            }

        logger.info(
            f"[clone] src={src_user_id} → dst={dst_user_id} agent_id={agent_id}"
        )

        snapshot_lease = None
        try:
            from .models.memory import MemoryLayer, MemoryNode

            # 1. 检查 dst 是否为空
            # 只看长度判空，不需要 embedding；避免为一次存在性探测拉回大 payload。
            # allow_raw_layer=True：判空要看"有没有任何数据"，含 raw；
            # 否则目标只有 raw 时会被误判为空，clone 覆盖上去造成数据混杂。
            dst_nodes = await self._vector_store.list_by_user(
                dst_user_id, agent_id=agent_id, limit=1, with_vectors=False,
                allow_raw_layer=True,
            )
            # L0_BASIC_INFO is user-scoped and is intentionally stored with an
            # empty agent_id. An agent-filtered clone must still treat an
            # existing user profile at the destination as data, otherwise it
            # can silently overwrite/mix user-level state.
            if agent_id and not dst_nodes:
                dst_user_nodes = await self._vector_store.list_by_user(
                    dst_user_id, layers=[MemoryLayer.L0_BASIC_INFO],
                    with_vectors=False, allow_raw_layer=True,
                )
                dst_nodes = [
                    node for node in dst_user_nodes
                    if not str(getattr(node, "agent_id", "") or "").strip()
                ][:1]
            if dst_nodes:
                return {
                    "success": False,
                    "request_id": request_id,
                    "cloned_count": 0,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "error_message": (
                        f"dst_user_id '{dst_user_id}' already has memories. "
                        "Clone requires an empty destination."
                    ),
                }

            # 2. 列出 src 的全量记忆（含 embedding）。同一 winner 同时
            # fan-out 到多个 rollout 时共享一次在途源快照，避免重复全库 scroll。
            # allow_raw_layer=True：clone 语义是"完整复制这个用户的记忆"，
            # 含原始对话层——目标用户的提取管线同样要靠 raw 拼对话历史，
            # 漏掉 raw 会让克隆出的用户丢失全部历史上下文。
            source_snapshot, snapshot_lease = await self._acquire_clone_source_snapshot(
                src_user_id, agent_id,
            )
            src_nodes = list(source_snapshot["nodes"])
            if not src_nodes:
                return {
                    "success": True,
                    "request_id": request_id,
                    "cloned_count": 0,
                    "by_layer": {},
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "error_message": None,
                }

            logger.info(f"[clone] found {len(src_nodes)} nodes to clone")

            # 3. 深拷贝：第一遍——生成新 node_id，建完整映射表
            id_map: Dict[str, str] = {}  # old_node_id → new_node_id

            for src_node in src_nodes:
                id_map[src_node.node_id] = str(uuid.uuid4())

            # 第二遍——用完整的 id_map 做深拷贝 + 指针重映射
            from .utils.clone_references import remap_memory_references
            new_nodes: List = []
            for src_node in src_nodes:
                new_id = id_map[src_node.node_id]

                # 使用 to_dict + from_dict 做深拷贝
                source_dict = src_node.to_dict()
                node_dict = remap_memory_references(source_dict, id_map)
                node_dict["node_id"] = new_id
                node_dict["user_id"] = dst_user_id
                # 重置检索元数据
                node_dict["access_count"] = 0
                node_dict["last_accessed_at"] = None

                # 映射演化链指针（supersedes / superseded_by）
                # 指向映射表外的 ID（不在本次 clone 范围内）则清空
                if source_dict.get("supersedes"):
                    node_dict["supersedes"] = [
                        id_map[sid] for sid in source_dict["supersedes"]
                        if sid in id_map
                    ]
                if source_dict.get("superseded_by"):
                    node_dict["superseded_by"] = [
                        id_map[sid] for sid in source_dict["superseded_by"]
                        if sid in id_map
                    ]
                # 映射 evidence_chain 中的 ID
                if source_dict.get("evidence_chain"):
                    node_dict["evidence_chain"] = [
                        id_map[eid] for eid in source_dict["evidence_chain"]
                        if eid in id_map
                    ]
                new_node = MemoryNode.from_dict(node_dict)
                # 保留原始 embedding（content 不变，无需重新计算）
                new_node.embedding = src_node.embedding
                # 保留原始时间（不要被 __post_init__ 覆盖）
                new_node.memory_at = src_node.memory_at
                new_node.gmt_created = src_node.gmt_created
                new_node.valid_from = src_node.valid_from
                new_nodes.append(new_node)

            # 5. 批量写入向量库
            written_ids = await self._vector_store.upsert_batch(new_nodes)
            logger.info(f"[clone] upsert_batch: {len(written_ids)} nodes written")
            written_id_set = set(written_ids)
            if written_id_set != {node.node_id for node in new_nodes}:
                raise RuntimeError("clone incomplete: not all destination nodes were written")
            clone_clauses = getattr(self._vector_store, "clone_retrieval_clauses", None)
            import_clauses = getattr(
                self._vector_store, "import_retrieval_clause_snapshot", None,
            )
            clause_snapshot = source_snapshot.get("retrieval_clauses")
            if clause_snapshot is not None and callable(import_clauses):
                await import_clauses(
                    clause_snapshot,
                    dst_user_id=dst_user_id,
                    id_map=id_map,
                )
            elif callable(clone_clauses):
                await clone_clauses(
                    src_user_id=src_user_id, dst_user_id=dst_user_id,
                    id_map=id_map, agent_id=agent_id,
                )
            by_layer: Dict[str, int] = {}
            for node in new_nodes:
                if node.node_id not in written_id_set:
                    continue
                layer = node.layer.value if hasattr(node.layer, "value") else str(node.layer)
                by_layer[layer] = by_layer.get(layer, 0) + 1

            # 6. 图数据库克隆（如启用）
            # 正确流程：从源 Graph 读节点 + 边，用 id_map 重映射后写入目标 Graph
            if self._graph_store is not None:
                graph_snapshot = source_snapshot.get("graph")
                graph_import = getattr(
                    self._graph_store, "import_clone_snapshot", None,
                )
                if graph_snapshot is not None and callable(graph_import):
                    try:
                        await graph_import(
                            graph_snapshot,
                            dst_user_id=dst_user_id,
                            id_map=id_map,
                            embed_service=self._embed_service,
                        )
                    except Exception as graph_error:
                        logger.warning(
                            "[clone] native Graph snapshot import failed; "
                            "falling back to legacy clone: %s", graph_error,
                        )
                        await self._clone_graph(
                            src_user_id=src_user_id,
                            dst_user_id=dst_user_id,
                            agent_id=agent_id,
                            id_map=id_map,
                        )
                else:
                    await self._clone_graph(
                        src_user_id=src_user_id,
                        dst_user_id=dst_user_id,
                        agent_id=agent_id,
                        id_map=id_map,
                    )

            # 7. 复制 memory_operations 记录（src → dst，用 id_map 重映射）
            if self._cache:
                try:
                    await self._clone_memory_operations(
                        src_user_id=src_user_id,
                        dst_user_id=dst_user_id,
                        clone_request_id=request_id,
                        id_map=id_map,
                    )
                except Exception as ops_err:
                    logger.warning(f"[clone] clone memory_operations failed (non-fatal): {ops_err}")

            logger.info(
                f"[clone] done: {len(written_ids)} nodes cloned "
                f"from {src_user_id} to {dst_user_id}"
            )

            return {
                "success": True,
                "request_id": request_id,
                "cloned_count": len(written_ids),
                "by_layer": by_layer,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "error_message": None,
            }

        except Exception as e:
            logger.error(f"[clone] failed: {e}", exc_info=True)
            # 回滚：删除已写入 dst 的数据
            try:
                await self._vector_store.delete_by_metadata(user_id=dst_user_id)
                delete_clauses = getattr(self._vector_store, "delete_retrieval_clauses_by_metadata", None)
                if callable(delete_clauses):
                    await delete_clauses(user_id=dst_user_id)
                await self._purge_graph_data(dst_user_id)
                logger.info(f"[clone] rollback: cleaned dst_user_id={dst_user_id}")
            except Exception as rollback_err:
                logger.warning(f"[clone] rollback failed: {rollback_err}")

            return {
                "success": False,
                "request_id": request_id,
                "cloned_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "error_message": str(e),
            }
        finally:
            if snapshot_lease is not None:
                await self._release_clone_source_snapshot(snapshot_lease)

    # ================================================================
    # Clone Graph Helper
    # ================================================================

    async def _clone_graph(
        self,
        src_user_id: str,
        dst_user_id: str,
        agent_id: Optional[str],
        id_map: Dict[str, str],
    ) -> None:
        """
        从源用户的 Graph 深拷贝到目标用户。

        流程：
        1. 读源 Graph 的所有 Memory 节点（按 isolation_key）
        2. 用 id_map 重映射节点 ID + user_id + isolation_key，写入目标 Graph
        3. 复制 VdbRef 影子节点（ID 重映射）+ DERIVED_FROM 边
        4. 复制 Memory→Memory 关系边（RELATED_TO）
        5. 复制 TAGGED_WITH 边（Topic tag）
        """
        from .models.memory import MemoryNode
        gs = self._graph_store
        if gs is None:
            return

        # 构建源/目标 isolation_key
        # 源用户可能有多个 session，用 get_all_nodes 按 user_id 前缀匹配
        # 但 get_all_nodes 需要精确 isolation_key，所以遍历 id_map 里的源节点找 isolation_key
        try:
            # Step 1: 读源 Graph 的所有 Memory 节点
            # 用 Cypher 按 user_id 查（不依赖 isolation_key，更通用）
            src_graph_nodes = []
            if hasattr(gs, '_execute') and hasattr(gs, '_available'):
                # Kuzu
                if not gs._available:
                    return
                result = gs._execute(
                    "MATCH (m:Memory) WHERE m.user_id = $uid RETURN m;",
                    {"uid": src_user_id},
                )
                while result is not None and result.has_next():
                    row = result.get_next()
                    src_graph_nodes.append(gs._row_to_memory_node(row[0]))
            elif hasattr(gs, '_run'):
                # Neo4j — 同时取 embedding / beh_embedding 以便 clone 时一并复制
                rows = await gs._run(
                    "MATCH (m:Memory) WHERE m.user_id = $uid "
                    "RETURN m, m.embedding AS _emb, m.beh_embedding AS _beh_emb",
                    {"uid": src_user_id},
                )
                for r in rows:
                    node = gs._record_to_memory_node(r["m"])
                    # 把 embedding 暂存到 node 上，供后续 upsert 使用
                    if r.get("_emb") is not None:
                        node._graph_embedding = list(r["_emb"])
                    if r.get("_beh_emb") is not None:
                        node._graph_beh_embedding = list(r["_beh_emb"])
                    src_graph_nodes.append(node)

            if not src_graph_nodes:
                logger.info("[clone] no Graph nodes to clone")
                return

            logger.info(f"[clone] Graph: found {len(src_graph_nodes)} source nodes")

            # 记住每个源节点的 old_id，确保 id_map 覆盖
            graph_old_ids = []  # 与 src_graph_nodes 一一对应
            for src_node in src_graph_nodes:
                old_nid = src_node.node_id
                graph_old_ids.append(old_nid)
                if old_nid not in id_map:
                    id_map[old_nid] = str(uuid.uuid4())

            # Step 2: 写入目标 Graph Memory 节点（深拷贝 + ID 重映射）
            from .models.memory import MemoryNode as _MN
            for i, src_node in enumerate(src_graph_nodes):
                old_nid = graph_old_ids[i]
                new_id = id_map[old_nid]
                # 深拷贝，不修改源 node
                from .utils.clone_references import remap_memory_references
                d = remap_memory_references(src_node.to_dict(), id_map)
                d["node_id"] = new_id
                d["user_id"] = dst_user_id
                dst_node = _MN.from_dict(d)
                # 携带源节点的 embedding（to_dict 不含 embedding，需手动传递）
                src_emb = getattr(src_node, '_graph_embedding', None)
                src_beh = getattr(src_node, '_graph_beh_embedding', None)
                if src_emb:
                    dst_node._graph_embedding = src_emb
                try:
                    await gs.upsert_memory_node(dst_node)
                    # upsert 后再补 beh_embedding（upsert 只写 embedding，不写 beh）
                    if src_beh:
                        await gs.update_embedding(new_id, beh_embedding=src_beh)
                except Exception as e:
                    logger.warning(f"[clone] Graph node upsert failed {new_id}: {e}")

            # Step 3: 复制 VdbRef + DERIVED_FROM 边
            for i, old_nid in enumerate(graph_old_ids):
                new_id = id_map[old_nid]
                try:
                    evidence = await gs.get_evidence_vdbrefs(old_nid)
                except Exception:
                    evidence = []

                for ev in evidence:
                    old_vdbref_id = ev["node_id"]
                    new_vdbref_id = id_map.get(old_vdbref_id, old_vdbref_id)
                    try:
                        await gs.ensure_vdbref(new_vdbref_id, ev.get("layer", ""))
                        await gs.add_derived_from(new_id, new_vdbref_id)
                    except Exception as e:
                        logger.debug(f"[clone] DERIVED_FROM edge failed: {e}")

            # Step 4: 复制 Memory→Memory 关系边。
            #
            # RELATED_TO 的 relation_type / weight 是读侧图重排的核心语义，
            # 不能只复制 Neo4j/Kuzu 的关系表类型。旧实现传空 properties，导致
            # semantic_neighbor/co_occurs/typed edges 全部退化成默认 related，
            # clone bank 与 seed bank 的检索行为因此不再等价。
            seen_relation_edges = set()

            for i, old_nid in enumerate(graph_old_ids):
                try:
                    if hasattr(gs, '_execute'):
                        result = gs._execute(
                            """
                            MATCH (a:Memory {node_id: $nid})-[r:RELATED_TO]->(b:Memory)
                            RETURN b.node_id, r.relation_type, r.weight;
                            """,
                            {"nid": old_nid},
                        )
                        edges = []
                        while result is not None and result.has_next():
                            row = result.get_next()
                            edges.append({
                                "target": row[0],
                                "edge_type": "RELATED_TO",
                                "properties": {
                                    "relation_type": row[1] or "related",
                                    "weight": float(row[2]) if row[2] is not None else 1.0,
                                },
                            })
                    elif hasattr(gs, '_run'):
                        rows = await gs._run(
                            """
                            MATCH (a:Memory {node_id: $nid})-[r:RELATED_TO]->(b:Memory)
                            RETURN b.node_id AS target, type(r) AS edge_type,
                                   properties(r) AS properties
                            """,
                            {"nid": old_nid},
                        )
                        edges = [{
                            "target": r["target"],
                            "edge_type": r["edge_type"],
                            "properties": {
                                "relation_type": (r.get("properties") or {}).get(
                                    "relation_type", "related"
                                ),
                                "weight": (r.get("properties") or {}).get("weight", 1.0),
                            },
                        } for r in rows]
                    else:
                        edges = []
                except Exception:
                    edges = []

                for edge in edges:
                    et = edge["edge_type"]
                    if et != "RELATED_TO":
                        continue
                    new_src = id_map.get(old_nid)
                    new_tgt = id_map.get(edge["target"])
                    if new_src and new_tgt:
                        properties = dict(edge.get("properties") or {})
                        relation_type = properties.get("relation_type") or "related"
                        edge_key = (
                            frozenset((old_nid, edge["target"])),
                            relation_type,
                        )
                        if edge_key in seen_relation_edges:
                            continue
                        seen_relation_edges.add(edge_key)
                        try:
                            await gs.add_edge(new_src, new_tgt, et, properties)
                        except Exception as e:
                            logger.debug(f"[clone] edge {et} failed: {e}")

            # Step 4b: 复制有向时序脊。这里只读 source 端，避免把同一有向边
            # 从两个端点各重放一次；topic_id 参与时间语义，必须保留。
            for old_nid in graph_old_ids:
                try:
                    if hasattr(gs, '_execute'):
                        result = gs._execute(
                            """
                            MATCH (a:Memory {node_id: $nid})-[r:TEMPORAL_NEXT]->(b:Memory)
                            RETURN b.node_id, r.topic_id;
                            """,
                            {"nid": old_nid},
                        )
                        temporal_edges = []
                        while result is not None and result.has_next():
                            row = result.get_next()
                            temporal_edges.append({"target": row[0], "topic_id": row[1] or ""})
                    elif hasattr(gs, '_run'):
                        temporal_edges = await gs._run(
                            """
                            MATCH (a:Memory {node_id: $nid})-[r:TEMPORAL_NEXT]->(b:Memory)
                            RETURN b.node_id AS target, r.topic_id AS topic_id
                            """,
                            {"nid": old_nid},
                        )
                    else:
                        temporal_edges = []
                except Exception:
                    temporal_edges = []
                for edge in temporal_edges:
                    new_src = id_map.get(old_nid)
                    new_tgt = id_map.get(edge["target"])
                    if new_src and new_tgt:
                        try:
                            await gs.add_temporal_edge(
                                new_src, new_tgt, edge.get("topic_id") or ""
                            )
                        except Exception as e:
                            logger.debug(f"[clone] TEMPORAL_NEXT edge failed: {e}")

            # Step 4c: 复制 System 2 的跨抽象有向边。
            for old_nid in graph_old_ids:
                try:
                    if hasattr(gs, '_execute'):
                        result = gs._execute(
                            """
                            MATCH (a:Memory {node_id: $nid})-[:CROSS_ABSTRACTS_TO]->(b:Memory)
                            RETURN b.node_id;
                            """,
                            {"nid": old_nid},
                        )
                        cross_targets = []
                        while result is not None and result.has_next():
                            cross_targets.append(result.get_next()[0])
                    elif hasattr(gs, '_run'):
                        rows = await gs._run(
                            """
                            MATCH (a:Memory {node_id: $nid})-[:CROSS_ABSTRACTS_TO]->(b:Memory)
                            RETURN b.node_id AS target
                            """,
                            {"nid": old_nid},
                        )
                        cross_targets = [row["target"] for row in rows]
                    else:
                        cross_targets = []
                except Exception:
                    cross_targets = []
                for old_tgt in cross_targets:
                    new_src = id_map.get(old_nid)
                    new_tgt = id_map.get(old_tgt)
                    if new_src and new_tgt:
                        try:
                            await gs.add_cross_abstracts_to(new_src, new_tgt)
                        except Exception as e:
                            logger.debug(f"[clone] CROSS_ABSTRACTS_TO edge failed: {e}")

            # Step 5: rebuild Topic and hierarchy membership in the destination
            # scope. Hierarchy node ids include user scope + subject + full path,
            # so copying source edges/ids would leak source-user identity.
            for i, old_nid in enumerate(graph_old_ids):
                new_id = id_map[old_nid]
                src_node = src_graph_nodes[i]
                dst_ik = MemoryNode.build_isolation_key(
                    dst_user_id,
                    src_node.agent_id or "default_agent",
                    src_node.session_id or "default_session",
                )

                try:
                    if hasattr(gs, '_execute'):
                        result = gs._execute(
                            "MATCH (m:Memory {node_id: $nid})-[:TAGGED_WITH]->(t:Topic) RETURN t.name;",
                            {"nid": old_nid},
                        )
                        tag_rows = []
                        while result is not None and result.has_next():
                            tag_rows.append({"name": result.get_next()[0], "embedding": None})
                    elif hasattr(gs, '_run'):
                        tag_rows = await gs._run(
                            "MATCH (m:Memory {node_id: $nid})-[:TAGGED_WITH]->(t:Topic) "
                            "RETURN t.name AS name, t.embedding AS embedding",
                            {"nid": old_nid},
                        )
                    else:
                        tag_rows = []
                except Exception:
                    tag_rows = []

                for tr in tag_rows:
                    tag_name = tr["name"]
                    tag_emb = tr.get("embedding")
                    try:
                        await gs.add_topic_tag(new_id, tag_name, dst_ik)
                        # 把源 Topic 的 embedding 写到目标 Topic（add_topic_tag 按 name
                        # 精确匹配时不设 embedding，需要补写）
                        if tag_emb is not None and hasattr(gs, '_run_write'):
                            await gs._run_write(
                                "MATCH (t:Topic {isolation_key: $ik, name: $name}) "
                                "WHERE t.embedding IS NULL "
                                "SET t.embedding = $emb",
                                {"ik": dst_ik, "name": tag_name, "emb": list(tag_emb)},
                            )
                    except Exception as e:
                        logger.debug(f"[clone] TAGGED_WITH failed: {e}")

                custom = getattr(src_node, "custom", None) or {}
                from .structure import topic_assignments_from_custom
                assignments = topic_assignments_from_custom(custom)
                if assignments and hasattr(gs, "add_topic_path"):
                    dst_topic_scope = MemoryNode.build_topic_scope_key(
                        dst_user_id, src_node.agent_id or "default_agent",
                    )
                    for assignment in assignments:
                        try:
                            try:
                                await gs.add_topic_path(
                                    new_id, assignment["path"], dst_topic_scope,
                                    assignment["subject"],
                                    node_types=assignment.get("node_types"),
                                    role=assignment.get("role") or "participant",
                                    embed_service=getattr(self, "_embed_service", None),
                                )
                            except TypeError:
                                await gs.add_topic_path(
                                    new_id, assignment["path"], dst_topic_scope,
                                    assignment["subject"],
                                    embed_service=getattr(self, "_embed_service", None),
                                )
                        except Exception as e:
                            logger.debug(f"[clone] hierarchy topic failed: {e}")

                # 正文 NER 是 normal recall 的独立加分通道。clone 后必须重建
                # MENTIONS，否则同一份 bank 仅因 user_id 改名就失去 entity recall。
                try:
                    if hasattr(gs, '_execute'):
                        result = gs._execute(
                            """
                            MATCH (m:Memory {node_id: $nid})-[:MENTIONS]->(e:Entity)
                            RETURN e.name, e.label;
                            """,
                            {"nid": old_nid},
                        )
                        entity_rows = []
                        while result is not None and result.has_next():
                            row = result.get_next()
                            entity_rows.append({"name": row[0], "label": row[1]})
                    elif hasattr(gs, '_run'):
                        entity_rows = await gs._run(
                            """
                            MATCH (m:Memory {node_id: $nid})-[:MENTIONS]->(e:Entity)
                            RETURN e.name AS name, e.label AS label
                            """,
                            {"nid": old_nid},
                        )
                    else:
                        entity_rows = []
                except Exception:
                    entity_rows = []
                for entity in entity_rows:
                    try:
                        await gs.add_entity_mention(
                            new_id, entity.get("name") or "",
                            entity.get("label") or "", dst_ik,
                        )
                    except Exception as e:
                        logger.debug(f"[clone] MENTIONS failed: {e}")

            logger.info(f"[clone] Graph: cloned {len(src_graph_nodes)} nodes with edges")

        except Exception as e:
            logger.warning(f"[clone] Graph clone failed (non-fatal): {e}")

    async def _clone_memory_operations(
        self,
        src_user_id: str,
        dst_user_id: str,
        clone_request_id: str,
        id_map: Dict[str, str],
    ) -> None:
        """
        复制源用户的 memory_operations 到目标用户。

        - user_id 替换为 dst_user_id
        - memory_id / old_memory_id 按 id_map 重映射（找不到映射的保留原值）
        - supersedes 列表中的 ID 也做重映射
        - request_id 保留源的（保持 per_request 分组语义）
        - 额外追加一条 CLONE op 记录（标记 clone 来源）
        """
        import json as _json

        # 1. 查出 src 的所有 ops
        src_ops = await self._cache.get_memory_operations(user_id=src_user_id, limit=50000)
        if not src_ops:
            logger.info("[clone] no memory_operations to clone")
            return

        # 2. 逐条写入 dst（ID 重映射）
        cloned_count = 0
        for op in src_ops:
            # 重映射 memory_id
            src_mem_id = op.get("memory_id", "")
            dst_mem_id = id_map.get(src_mem_id, src_mem_id)

            # 重映射 old_memory_id
            src_old_id = op.get("old_memory_id") or ""
            dst_old_id = id_map.get(src_old_id, src_old_id) if src_old_id else None

            # 重映射 supersedes 列表
            raw_supersedes = op.get("supersedes", [])
            if isinstance(raw_supersedes, str):
                try:
                    raw_supersedes = _json.loads(raw_supersedes)
                except Exception:
                    raw_supersedes = []
            dst_supersedes = [id_map.get(sid, sid) for sid in (raw_supersedes or [])]

            await self._cache.store_memory_operation(
                request_id=op.get("request_id", clone_request_id),
                user_id=dst_user_id,
                agent_id=op.get("agent_id", ""),
                op=op.get("op", "ADD"),
                memory_id=dst_mem_id,
                content=op.get("content", ""),
                layer=op.get("layer", ""),
                old_memory_id=dst_old_id,
                reason=op.get("reason", ""),
                supersedes=dst_supersedes,
            )
            cloned_count += 1

        # 3. 追加一条 CLONE 元记录（标记 clone 来源，方便回溯）
        await self._cache.store_memory_operation(
            request_id=clone_request_id,
            user_id=dst_user_id,
            agent_id="",
            op="CLONE",
            memory_id="",
            content=_json.dumps({
                "src_user_id": src_user_id,
                "dst_user_id": dst_user_id,
                "ops_cloned": cloned_count,
            }, ensure_ascii=False),
            layer="",
            reason=f"cloned from {src_user_id}",
        )

        logger.info(f"[clone] memory_operations: cloned {cloned_count} ops from {src_user_id} → {dst_user_id}")

    # ================================================================
    # History API（异步）
    # ================================================================

    @_ensure_internal_loop
    async def async_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """查询某条记忆的变更历史（异步）"""
        if self._history_store is None:
            return []
        return await self._history_store.get_history(memory_id)

    @_ensure_internal_loop
    async def async_get_recent_history(
        self,
        user_id: str = "",
        agent_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询最近的操作历史（异步）。

        agent_ids 为空时，查询该用户所有 agent 的历史。
        """
        if self._history_store is None:
            return []

        uid = user_id
        effective_agent_ids = agent_ids if agent_ids is not None else []

        if not effective_agent_ids:
            # 查所有 agent 的历史：用 "user_id:" 前缀匹配
            isolation_key = self._build_isolation_key(uid, "default")
            return await self._history_store.get_recent(isolation_key, limit=limit)
        else:
            # 查指定 agent 的历史
            all_results = []
            for aid in effective_agent_ids:
                isolation_key = self._build_isolation_key(uid, aid)
                results = await self._history_store.get_recent(isolation_key, limit=limit)
                all_results.extend(results)
            # 按时间倒序排列，取 top limit
            all_results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return all_results[:limit]

    # ================================================================
    # Context manager
    # ================================================================

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._close_async()
