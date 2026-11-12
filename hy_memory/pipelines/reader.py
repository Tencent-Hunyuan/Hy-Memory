"""
HY Memory - Read (Dispatcher)

对外统一入口 `MemoryReader`，根据环境变量 `HY_MEMORY_READER` 选择并代理
真正的实现：

  - "legacy" → LegacyReadPipeline  (默认，向量候选召回)
  - "hybrid" → HybridReadPipeline  (向量 + BM25 候选召回)

两者共享同一套后处理：profile/intention、episode、tag/entity、graph、
local rerank、演化链、配额与结果格式化。

cache 参数从 Registry 注入，用于 read 侧的 trace 写入；传 None 时
trace 写入静默跳过。
"""

from typing import Any, Optional
import logging

from .base import ReadPipeline, ReadRequest, ReadResponse, PipelineContext
from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store_base import VectorStoreBase
from ..data.graph_store_base import GraphStoreBase
from ..utils.tracer import PipelineTracer
from ._retrieval import config as _retrieval_config

logger = logging.getLogger(__name__)


def _build_impl(
    config: MemoryConfig,
    embed_service: Optional[EmbedService],
    vector_store: Optional[VectorStoreBase],
    graph_store: Optional[GraphStoreBase] = None,
    cache: Any = None,
    reader_name: str = "",
) -> ReadPipeline:
    """按环境变量选择内部实现类并构造实例。"""
    name = _retrieval_config.resolve_reader_name(reader_name)
    if name == _retrieval_config.READER_NATIVE:
        from .reader_native import NativeReadPipeline
        return NativeReadPipeline(config, embed_service, vector_store)
    if name == _retrieval_config.READER_HYBRID:
        try:
            from .reader_hybrid import HybridReadPipeline
            return HybridReadPipeline(
                config, embed_service, vector_store,
                graph_store=graph_store, cache=cache,
            )
        except ImportError as e:
            logger.warning(f"[reader-dispatch] hybrid import failed: {e}; fallback to legacy")

    # 默认 / fallback
    from .reader_legacy import LegacyReadPipeline
    return LegacyReadPipeline(
        config, embed_service, vector_store,
        graph_store=graph_store, cache=cache,
    )


class MemoryReader(ReadPipeline):
    """
    读取器（对外入口）。

    按环境变量 `HY_MEMORY_READER` 分发到不同实现：
      - legacy / hybrid
    默认 legacy，行为与 post13 之前一致。
    """

    VERSION = "reader"

    def __init__(
        self,
        config: MemoryConfig,
        embed_service: Optional[EmbedService] = None,
        vector_store: Optional[VectorStoreBase] = None,
        graph_store: Optional[GraphStoreBase] = None,
        cache: Any = None,
        reader_name: str = "",
    ):
        self.config = config
        self._impl: ReadPipeline = _build_impl(
            config, embed_service, vector_store, graph_store=graph_store, cache=cache,
            reader_name=reader_name,
        )
        logger.info(
            f"[reader-dispatch] active impl = {self._impl.__class__.__name__} "
            f"(HY_MEMORY_READER={_retrieval_config.resolve_reader_name()}) "
            f"graph_store={'yes' if graph_store else 'no'}"
        )

    async def initialize(self) -> None:
        await self._impl.initialize()

    async def read(
        self,
        request: ReadRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> ReadResponse:
        return await self._impl.read(request, ctx=ctx, tracer=tracer)

    async def close(self) -> None:
        await self._impl.close()

    # ---- 便捷属性：暴露内部实现给诊断脚本 ----
    @property
    def impl(self) -> ReadPipeline:
        return self._impl

    @property
    def embed_service(self) -> EmbedService:
        return getattr(self._impl, "embed_service", None)
