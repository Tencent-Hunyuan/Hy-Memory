"""
Read pipeline 相关环境变量与常量集中入口。

所有参数优先级：
  进程环境变量 > 代码默认常量

约定所有环境变量前缀 `HY_MEMORY_READER_` 与既有 `HY_MEMORY_*` 家族对齐。
"""

import os

from ...models.memory import MemoryLayer


# ========================================================================
# Reader 分发
# ========================================================================

READER_LEGACY = "legacy"
READER_HYBRID = "hybrid"
READER_NATIVE = "native"

ALL_READERS = (READER_LEGACY, READER_HYBRID, READER_NATIVE)


VDB_RECALL_LAYERS = [MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY, MemoryLayer.L4_IDENTITY]


def resolve_reader_name(override: str = "") -> str:
    """
    解析最终使用的 reader 名称：显式 override > 环境变量 > 默认 legacy。
    未知值 fallback 到 legacy 并输出 warning。
    """
    name = (override or os.environ.get("HY_MEMORY_READER", "") or READER_LEGACY).strip().lower()
    if name not in ALL_READERS:
        import logging
        logging.getLogger(__name__).warning(
            f"[reader-config] unknown HY_MEMORY_READER={name!r}, fallback to {READER_LEGACY}"
        )
        return READER_LEGACY
    return name


# ========================================================================
# 召回池子大小
# ========================================================================

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


VEC_POOL_SIZE: int = _int_env("HY_MEMORY_READER_VEC_POOL_SIZE", 30)
"""路 A（主向量召回）的候选池上限。"""

TAG_POOL_SIZE: int = _int_env("HY_MEMORY_READER_TAG_POOL_SIZE", 20)
"""路 B（tag 语义桥接召回）的候选池上限。"""

TAG_MATCH_TOPK: int = _int_env("HY_MEMORY_READER_TAG_MATCH_TOPK", 5)
"""每个 query keyword 匹配的 tag 数上限。路 B 的主截断就在这里——实际命中多少 tag
以 topk 为准，min_score 只做软过滤防止完全不沾边的噪声混进来。"""

TAG_MATCH_MIN_SCORE: float = _float_env("HY_MEMORY_READER_TAG_MATCH_MIN_SCORE", 0.3)
"""tag 匹配的软过滤地板线（默认 0.3）。

语义：**topk 为主，min_score 为辅**——先按 cosine 取 topk，再把 < min_score 的剔掉。
不做 Qdrant 服务端硬过滤，避免"user 没有高度相关 tag 时整体空召回"。

tag embedding 本身是单词/短语粒度的 embedding，信噪比天然弱（`food` 和 `beef noodle`
的 cosine 可能只有 0.45~0.55），阈值设高容易全军覆没。post16 起从 0.5 调到 0.3。"""

ABSTAIN_THRESHOLD: float = _float_env("HY_MEMORY_READER_ABSTAIN_THRESHOLD", 0.15)
"""弃权/低置信阈值：top3 平均 RRF 分数 低于此值时标记 is_low_confidence。"""

KEYWORD_MAX_COUNT: int = _int_env("HY_MEMORY_READER_KEYWORD_MAX_COUNT", 10)
"""query 最多提取多少个 keyword 参与路 B batch embed。"""


# ========================================================================
# RRF 融合
# ========================================================================

RRF_K: int = _int_env("HY_MEMORY_READER_RRF_K", 60)
"""RRF 平滑常数，Cormack et al. 2009 经典值。"""


# ========================================================================
# BM25-lite 参数
# ========================================================================

BM25_K1: float = _float_env("HY_MEMORY_READER_BM25_K1", 1.5)
BM25_B: float = _float_env("HY_MEMORY_READER_BM25_B", 0.75)


# ========================================================================
# Tag index 存储
# ========================================================================

def tag_index_collection_name(memory_collection: str) -> str:
    """tag_index 独立 collection 命名规则。"""
    return f"{memory_collection}_tag_index"


def entity_collection_name(memory_collection: str) -> str:
    """entity store 独立 collection 命名规则（对齐 mem0 的 {collection}_entities）。"""
    return f"{memory_collection}_entities"


# intent_override 调试用（运行时强制指定意图）
INTENT_OVERRIDE: str = (os.environ.get("HY_MEMORY_READER_INTENT_OVERRIDE", "") or "").strip().upper()


# 强开关：允许业务方完全关闭 tag_index 维护（例如存储压力评估期）
# 关闭后 writer 不再维护 tag_index。
TAG_INDEX_WRITE_ENABLED: bool = (
    (os.environ.get("HY_MEMORY_TAG_INDEX_WRITE", "true") or "true").strip().lower() not in ("0", "false", "off", "no")
)
