# -*- coding: utf-8 -*-
"""
Qdrant BM25 稀疏向量编码器（fastembed Qdrant/bm25）单例封装。

Qdrant 原生 BM25 走 sparse vector：
  - 写入：文档文本 → fastembed .embed() → sparse(indices, values)，含 TF 权重
  - 检索：query → .query_embed() → sparse，query_points(using="bm25")
  - IDF：由 Qdrant sparse vector 的 ``modifier=IDF`` 根据 collection 统计计算

BM25 是非对称的：文档侧用 embed()（TF 权重），查询侧用 query_embed()
（权重为 1）。两者不可混用。

SparseTextEmbedding('Qdrant/bm25') 首次会下载约 10 MB 的 tokenizer /
stop-word 资源，必须进程内单例缓存。fastembed 缺失时优雅降级：返回 None，
调用方据此关闭 qdrant sparse 能力。
"""

from __future__ import annotations

import logging
import threading
from importlib.util import find_spec
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# (indices, values)：可直接用于 qdrant SparseVector(indices=, values=)
SparseTuple = Tuple[List[int], List[float]]

_encoder = None            # SparseTextEmbedding | None | False(=不可用)
_lock = threading.Lock()

_MODEL_NAME = "Qdrant/bm25"


def package_available() -> bool:
    """Return whether FastEmbed is installed without loading/downloading a model."""
    return find_spec("fastembed") is not None


def _get_encoder():
    """懒加载 fastembed BM25 单例。不可用时返回 False。"""
    global _encoder
    if _encoder is not None:
        return _encoder
    with _lock:
        if _encoder is not None:
            return _encoder
        try:
            from fastembed import SparseTextEmbedding
            enc = SparseTextEmbedding(_MODEL_NAME)
            logger.info(f"[bm25-fastembed] SparseTextEmbedding('{_MODEL_NAME}') loaded")
            _encoder = enc
        except Exception as e:
            logger.warning(
                f"[bm25-fastembed] fastembed unavailable ({e}); "
                f"qdrant sparse BM25 disabled, keyword channel will be empty. "
                f"pip install fastembed"
            )
            _encoder = False
    return _encoder


def is_available() -> bool:
    """fastembed BM25 编码器是否可用。"""
    return bool(_get_encoder())


def _to_tuple(sparse_embedding) -> Optional[SparseTuple]:
    """fastembed SparseEmbedding → (indices:List[int], values:List[float])。"""
    try:
        indices = [int(i) for i in sparse_embedding.indices]
        values = [float(v) for v in sparse_embedding.values]
        if not indices:
            return None
        return indices, values
    except Exception as e:
        logger.debug(f"[bm25-fastembed] sparse convert failed: {e}")
        return None


def encode_doc(text: str) -> Optional[SparseTuple]:
    """Encode one document; only a successful zero-token result is ``None``."""
    if not text:
        return None
    enc = _get_encoder()
    if not enc:
        raise RuntimeError("BM25 encoder is unavailable")
    try:
        out = list(enc.embed([text]))
        return _to_tuple(out[0]) if out else None
    except Exception as e:
        logger.warning(f"[bm25-fastembed] encode_doc failed: {e}")
        raise RuntimeError("BM25 document encoding failed") from e


def encode_docs(texts: List[str]) -> List[Optional[SparseTuple]]:
    """Batch encode documents; model/runtime failures are retryable errors.

    A successfully encoded zero-token document is represented by ``None``.
    Exceptions from FastEmbed must propagate so maintenance callers do not
    persist an empty sparse marker for a transient encoder failure.
    """
    enc = _get_encoder()
    if not enc:
        raise RuntimeError("BM25 encoder is unavailable")
    idxs = [i for i, t in enumerate(texts) if t]
    result: List[Optional[SparseTuple]] = [None] * len(texts)
    if not idxs:
        return result
    try:
        encoded = list(enc.embed([texts[i] for i in idxs]))
        for j, i in enumerate(idxs):
            if j < len(encoded):
                result[i] = _to_tuple(encoded[j])
    except Exception as e:
        logger.warning(f"[bm25-fastembed] encode_docs failed: {e}")
        raise RuntimeError("BM25 batch encoding failed") from e
    return result


def encode_query(text: str) -> Optional[SparseTuple]:
    """查询侧编码（query_embed，权重为 1）；不可用或空文本返回 None。"""
    enc = _get_encoder()
    if not enc or not text:
        return None
    try:
        out = list(enc.query_embed(text))
        return _to_tuple(out[0]) if out else None
    except Exception as e:
        logger.debug(f"[bm25-fastembed] encode_query failed: {e}")
        return None
