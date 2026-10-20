"""
HY Memory - Component Factory

ComponentFactory 负责:
1. 管理共享资源 (EmbedService, VectorStore, GraphStore, Cache)
2. 创建 MemoryWriter / MemoryReader / System2Writer 实例（单例缓存）
3. 管理组件生命周期

使用方式:
    factory = ComponentFactory(config=config)
    writer = await factory.get_writer()
    reader = await factory.get_reader()
    system2 = await factory.get_system2_writer()
"""

from typing import Optional, Dict
import logging

from .base import WritePipeline, ReadPipeline
from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store import create_vector_store
from ..data.vector_store_base import VectorStoreBase

from ..utils.init_rollback import close_component

logger = logging.getLogger(__name__)


# ================================================================
# 向后兼容别名 — 供旧代码 / 外部 import 使用
# ================================================================

class PipelineConfig:
    """向后兼容：已废弃，保留避免 ImportError。"""
    def __init__(self, **kwargs):
        pass


class ComponentFactory:
    """
    组件工厂

    管理 Writer / Reader / System2Writer 实例及共享资源。
    client.py 通过此工厂创建和获取组件。

    使用方式:
        factory = ComponentFactory(config=config)
        writer = await factory.get_writer()       # MemoryWriter
        reader = await factory.get_reader()        # MemoryReader (default)
        reader = await factory.get_reader(reader_name="hybrid")     # 指定 reader
        s2 = await factory.get_system2_writer()    # System2Writer (ultra mode)
    """

    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig.from_env()

        # 实例缓存（单例）
        self._writer: Optional[WritePipeline] = None
        self._reader: Optional[ReadPipeline] = None
        self._system2_writer: Optional[WritePipeline] = None

        # 多 reader 缓存（按 reader_name 索引）
        self._readers: Dict[str, ReadPipeline] = {}

        # 共享资源
        self._shared_embed_service: Optional[EmbedService] = None
        self._shared_vector_store: Optional[VectorStoreBase] = None
        self._shared_vector_store_initialized: bool = False
        self._shared_graph_store = None
        self._shared_cache = None

    # ================================================================
    # 共享资源
    # ================================================================

    def _get_shared_embed_service(self) -> EmbedService:
        if self._shared_embed_service is None:
            self._shared_embed_service = EmbedService(self.config)
        return self._shared_embed_service

    def _get_shared_vector_store(self) -> VectorStoreBase:
        if self._shared_vector_store is None:
            self._shared_vector_store = create_vector_store(self.config)
            logger.debug("ComponentFactory: VectorStore created")
        return self._shared_vector_store

    # ================================================================
    # 获取组件（自动初始化 + 缓存）
    # ================================================================

    async def get_writer(self, **kwargs) -> WritePipeline:
        """
        获取 MemoryWriter 实例（System 1 写入器）。

        所有 mode (lite/pro/ultra) 都使用同一个 MemoryWriter，
        区别只在于 client 传入的 agent_mode 参数。
        """
        if self._writer is not None:
            return self._writer

        from .writer import MemoryWriter
        writer = MemoryWriter(
            config=self.config,
            embed_service=self._get_shared_embed_service(),
            vector_store=self._get_shared_vector_store(),
            cache=self._shared_cache,
            # 记忆关系图（pro 模式）：写侧把 L2_FACT 投影进图并建 RELATED_TO / TAGGED_WITH。
            # None 时 writer 里的图投影整段跳过。
            graph_store=self._shared_graph_store,
        )
        try:
            await writer.initialize()
        except BaseException:
            await close_component("partial writer", writer.close)
            raise
        self._writer = writer
        logger.debug("MemoryWriter initialized")
        return writer

    async def get_reader(self, reader_name: str = "", **kwargs) -> ReadPipeline:
        """
        获取 MemoryReader 实例。

        Args:
            reader_name: 指定 reader 类型（legacy / hybrid）。
                         空字符串 = 使用 HY_MEMORY_READER 环境变量或默认 legacy。

        支持按请求动态切换 reader，每种 reader 缓存一个实例。
        """
        # 确定实际的 reader name
        from ._retrieval.config import resolve_reader_name
        effective_name = resolve_reader_name(reader_name)

        # 检查缓存
        if effective_name in self._readers:
            return self._readers[effective_name]

        # 向后兼容：无指定时走旧的 _reader 单例
        if not reader_name and self._reader is not None:
            return self._reader

        reader_kwargs = {
            "config": self.config,
            "embed_service": self._get_shared_embed_service(),
            "vector_store": self._get_shared_vector_store(),
            "cache": self._shared_cache,
        }
        if self._shared_graph_store is not None:
            reader_kwargs["graph_store"] = self._shared_graph_store

        # 用 _build_impl 直接构建指定 reader（绕过 MemoryReader dispatcher）
        import os
        old_env = os.environ.get("HY_MEMORY_READER", "")
        os.environ["HY_MEMORY_READER"] = effective_name
        try:
            from .reader import MemoryReader
            reader = MemoryReader(**reader_kwargs)
            try:
                await reader.initialize()
            except BaseException:
                await close_component("partial reader", reader.close)
                raise
        finally:
            if old_env:
                os.environ["HY_MEMORY_READER"] = old_env
            else:
                os.environ.pop("HY_MEMORY_READER", None)

        # 缓存
        self._readers[effective_name] = reader
        if not reader_name:
            self._reader = reader
        logger.debug(f"MemoryReader initialized: {effective_name}")
        return reader

    async def get_system2_writer(self, **kwargs) -> WritePipeline:
        """
        获取 System2Writer 实例（ultra mode 专用）。

        System2Writer 内部包含 MemoryWriter (System 1) + System 2 认知加工。
        """
        if self._system2_writer is not None:
            return self._system2_writer

        from .system2_writer import System2Writer
        s2_kwargs = {
            "config": self.config,
            "embed_service": self._get_shared_embed_service(),
            "vector_store": self._get_shared_vector_store(),
            "cache": self._shared_cache,
        }
        if self._shared_graph_store is not None:
            s2_kwargs["graph_store"] = self._shared_graph_store
        s2 = System2Writer(**s2_kwargs)
        try:
            await s2.initialize()
        except BaseException:
            await close_component("partial s2", s2.close)
            raise
        self._system2_writer = s2
        logger.debug("System2Writer initialized")
        return s2

    # ================================================================
    # 生命周期
    # ================================================================

    async def close(self) -> None:
        """关闭所有组件和共享资源"""
        for name, inst in [("writer", self._writer), ("reader", self._reader),
                           ("system2_writer", self._system2_writer)]:
            if inst is not None:
                try:
                    await inst.close()
                except Exception as e:
                    logger.error(f"Failed to close {name}: {e}")

        if self._shared_vector_store:
            try:
                await self._shared_vector_store.close()
            except Exception as e:
                logger.error(f"Failed to close VectorStore: {e}")

        self._writer = None
        self._reader = None
        self._system2_writer = None
        self._shared_vector_store = None
        self._shared_vector_store_initialized = False
        self._shared_embed_service = None

        logger.debug("ComponentFactory closed")


# ================================================================
# 向后兼容别名
# ================================================================

PipelineRegistry = ComponentFactory
