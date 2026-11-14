# -*- coding: utf-8 -*-
"""
SharedRuntime — 多 client 部署的共享基础设施。

## 背景

`HyMemoryClient` 默认每个实例自带：
  - 一个 `_LoopThread`（持久 event loop）
  - 一份 `MysqlCache` / `SqliteCache` pool
  - 一份 pipeline observability hook 包装

而 `MetricsCollector` 是进程级 singleton。当应用因不同数据域的 embedder dims
不同而**起多个 client** 时：

```
Client-A._LoopThread (loop_A)   →  Pool-A bound to loop_A
Client-B._LoopThread (loop_B)   →  Pool-B bound to loop_B

MetricsCollector (singleton)
  ├── bind_cache(Pool-A)  ← Client-A init 时
  ├── bind_cache(Pool-B)  ← Client-B init 时（覆盖！）
  └── flush coroutine    ← ensure_future on loop_A，但访问 Pool-B
                          → 跨 loop 用 aiomysql → 连接污染 → KeyError
```

## 设计

把"进程级资源"显式抽出来：

| 归属 SharedRuntime（一进程一份） | 归属每个 HyMemoryClient |
|---|---|
| `_LoopThread`（共享 loop）         | `_embed_service`（不同 dims）         |
| `MysqlCache` / `SqliteCache` pool | `_vector_store`（不同 collection）     |
| `MetricsCollector` 单例 + 唯一 bind | `_graph_store`                        |
| pipeline observability hook       | `_history_store` / `_coding_store`    |
|                                   | `_registry` + 所有 pipeline           |

## 使用

```python
from hy_memory import HyMemoryClient, SharedRuntime, MemoryConfig

# App 启动时一次性创建
base_config = MemoryConfig.from_env()  # 提供 cache 配置
runtime = await SharedRuntime.create(base_config)

# 每个配置域一个 lightweight client，共用 runtime
clients = {
    "default": HyMemoryClient(default_config, runtime=runtime),
    "research": HyMemoryClient(research_config, runtime=runtime),
}

# App 退出
for c in clients.values():
    c.close()              # 只关该 client 的资源
await runtime.aclose()     # 关 shared 基础设施
```

## 兼容性

不传 `runtime=` 的旧用法**完全不变**，client 自己持有所有资源（solo 模式）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .utils.init_rollback import (
    close_component,
    finish_cleanup,
    initialize_component,
    rollback_metrics,
    run_initialization,
    stop_failed_loops,
)

if TYPE_CHECKING:
    from .config import MemoryConfig

logger = logging.getLogger(__name__)


class SharedRuntime:
    """
    多 client 共享的基础设施。

    用 `await SharedRuntime.create(config)` 构造（不要直接 `SharedRuntime(config)`，
    需要 async init 才能建 cache pool 并 bind metrics）。
    """

    def __init__(self, config: "MemoryConfig"):
        # 配置只用来建 cache 和决定 loop 策略；vector_store / embedder 等
        # 由各个 client 自己管。
        self._config = config
        self._loop_thread = None  # type: Optional[object]  # _LoopThread（write/evolve/后台）
        # search 专用 loop：与 write loop 物理隔离，避免 write 队列挤占 search 调度。
        self._search_loop_thread = None  # type: Optional[object]  # _LoopThread
        self._cache = None        # type: Optional[CacheBase]
        self._initializing_metrics = None
        self._metrics = None      # type: Optional[object]  # MetricsCollector
        self._observability_installed = False
        self._closed = False

    # ------------------------------------------------------------
    # 工厂 / 生命周期
    # ------------------------------------------------------------

    @classmethod
    async def create(cls, config: "MemoryConfig") -> "SharedRuntime":
        """
        创建并初始化一个 SharedRuntime。

        会立即：
          1. 起一个 _LoopThread（持久 event loop）
          2. 在该 loop 上 init cache pool
          3. 把 cache 绑给 MetricsCollector 单例
          4. 在该 loop 上启动 metrics 后台 flush + cleanup
          5. 把 pipeline observability hook 安装到 cache 上

        步骤 2-5 都在同一个 loop（即 _LoopThread 的 loop）上执行，
        保证后续 metrics flush 用的就是这个 loop 上的 pool，不会跨 loop。
        """
        rt = cls(config)
        await rt._initialize()
        return rt

    async def _initialize(self) -> None:
        # 注意：本方法可能从外部 loop 调到（如 app 的 uvicorn loop），
        # 但内部所有"绑定到 loop"的操作必须在 _LoopThread 的 loop 上完成。
        # 因此把真正的 init 委托过去。
        from .client import _LoopThread

        try:
            self._loop_thread = _LoopThread()
            self._search_loop_thread = _LoopThread()
            await run_initialization(
                self._loop_thread, self._initialize_components, self._rollback_initialization,
            )
            self._initializing_metrics = None
        except BaseException:
            stop_failed_loops(self._search_loop_thread, self._loop_thread)
            self._closed = True
            raise

    async def _initialize_components(self) -> None:
        try:
            await self._init_on_loop()
        except BaseException:
            await finish_cleanup(self._rollback_initialization())
            raise

    async def _rollback_initialization(self) -> None:
        state, self._initializing_metrics = self._initializing_metrics, None
        await rollback_metrics(state, self._cache)
        self._metrics = None
        cache, self._cache = self._cache, None
        if cache is not None:
            await close_component("cache", cache.close)

    async def _init_on_loop(self) -> None:
        """在 _LoopThread 的 loop 上执行的初始化。"""
        # 1. cache pool（pool 会绑定到当前 loop）
        from .data.cache import create_cache

        self._cache = create_cache(self._config)
        await initialize_component(self._cache)
        logger.info(
            f"[runtime] Cache ready (backend={self._config.cache.backend})"
        )

        # 2. MetricsCollector 单例：唯一一次 bind_cache + start_background_tasks
        #    flush 协程从此固定在本 loop 上跑，永远不会跨 loop。
        from .metrics import MetricsCollector

        self._metrics = MetricsCollector.get()
        self._initializing_metrics = (
            self._metrics, self._metrics._cache, self._metrics._background_started,
        )
        self._metrics.bind_cache(self._cache)
        await self._metrics.start_background_tasks()
        logger.info("[runtime] MetricsCollector bound to shared cache")

        # 3. pipeline observability hook — 包一次就够，所有 client 共用
        self._install_pipeline_observability()
        logger.info("[runtime] SharedRuntime ready")

    def _install_pipeline_observability(self) -> None:
        """与 HyMemoryClient._install_pipeline_observability_hooks 等价，
        但只装一次。重复装会导致每个 store_pipeline_log 调用被多层包装。"""
        if self._observability_installed:
            return
        self._observability_installed = True

        from .utils.pipeline_observability import install_pipeline_observability

        install_pipeline_observability(self._cache, log_label="runtime")

    # ------------------------------------------------------------
    # 暴露给 HyMemoryClient 的属性（read-only）
    # ------------------------------------------------------------

    @property
    def loop_thread(self):
        """共享的 _LoopThread 实例（write / evolve / 后台任务）。"""
        return self._loop_thread

    @property
    def search_loop_thread(self):
        """search 专用的 _LoopThread 实例，与 write loop 物理隔离。"""
        return self._search_loop_thread

    @property
    def cache(self):
        """共享的 cache（已 initialize 完毕）。"""
        return self._cache

    @property
    def metrics(self):
        """MetricsCollector 单例（已 bind_cache 到 self.cache）。"""
        return self._metrics

    @property
    def config(self) -> "MemoryConfig":
        """构造 runtime 时用的 base config（client 不必跟它一致，仅 cache 配置须一致）。"""
        return self._config

    # ------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------

    async def aclose(self) -> None:
        """异步关闭 shared 资源（cache + loop）。"""
        if self._closed:
            return
        self._closed = True

        if self._metrics is not None:
            try:
                await self._loop_thread.run_async(
                    self._metrics.stop_background_tasks()
                )
            except Exception as e:
                logger.warning(f"[runtime] metrics close failed: {e}")
            self._metrics = None

        if self._cache is not None:
            try:
                # cache 在 _LoopThread 的 loop 上建的，关也得在它上面关
                await self._loop_thread.run_async(self._cache.close())
            except Exception as e:
                logger.warning(f"[runtime] cache close failed: {e}")
            self._cache = None

        if self._loop_thread is not None:
            try:
                self._loop_thread.stop()
            except Exception as e:
                logger.warning(f"[runtime] loop_thread stop failed: {e}")
            self._loop_thread = None

        if self._search_loop_thread is not None:
            try:
                self._search_loop_thread.stop()
            except Exception as e:
                logger.warning(f"[runtime] search_loop_thread stop failed: {e}")
            self._search_loop_thread = None

        logger.info("[runtime] SharedRuntime closed")

    def close(self) -> None:
        """同步关闭，方便从非 async 场景调用。"""
        if self._closed:
            return
        if self._loop_thread is not None:
            try:
                # cache.close 必须在它绑定的 loop 上跑
                self._loop_thread.run(self._aclose_inner())
            except Exception as e:
                logger.warning(f"[runtime] sync close inner failed: {e}")
            try:
                self._loop_thread.stop()
            except Exception as e:
                logger.warning(f"[runtime] loop_thread stop failed: {e}")
        if self._search_loop_thread is not None:
            try:
                self._search_loop_thread.stop()
            except Exception as e:
                logger.warning(f"[runtime] search_loop_thread stop failed: {e}")
        self._loop_thread = None
        self._search_loop_thread = None
        self._cache = None
        self._closed = True
        logger.info("[runtime] SharedRuntime closed")

    async def _aclose_inner(self) -> None:
        """在 loop 内执行的关闭流程（仅 close cache，不停 loop —— stop 由外面来做）。"""
        if self._metrics is not None:
            try:
                await self._metrics.stop_background_tasks()
            except Exception as e:
                logger.warning(f"[runtime] metrics close failed: {e}")
            self._metrics = None
        if self._cache is not None:
            try:
                await self._cache.close()
            except Exception as e:
                logger.warning(f"[runtime] cache close failed: {e}")
