"""
Agent Memory - EmbedService 向量化服务

将文本转换为向量表示。

功能：
- 文本向量化
- 批量向量化
- 多模型支持
- 缓存机制
- 写路径攒批队列（embed_queued）：将高并发的单条 embed 请求攒成 batch 后一次发送，
  避免大量并发 write 请求同时打爆 embedding 服务。search 路径用 embed() 不走队列。

示例：
    embed_service = EmbedService(config)
    
    # 单条向量化（search 路径，直接并发）
    vector = await embed_service.embed("用户喜欢川菜")
    
    # 写路径攒批向量化（write 路径，通过队列攒批后发送）
    vector = await embed_service.embed_queued("用户喜欢川菜")
    
    # 批量向量化
    vectors = await embed_service.embed_batch([
        "用户喜欢川菜",
        "用户住在北京",
    ])
"""

from typing import Dict, Any, Optional, List, Tuple
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import asyncio
import hashlib
import importlib.util
import json
from json import JSONDecodeError
import logging
import threading
import time

from ..config import MemoryConfig
from ..embedding import EmbeddingDimensionMismatchError
from .workload import embedding_slot
from ..utils.log_setup import get_request_id

logger = logging.getLogger(__name__)

# ── embedding 输入长度上限 ────────────────────────────────────────────────────
#
# text-embedding-3-small 的硬上限是 8192 token，超一个 token 就整条 400：
#   Invalid 'input[0]': maximum input length is 8192 tokens
#
# 为什么必须在这一层兜：检索侧可能把包含长背景、公式或代码的整段 query
# 直接送入 embedding endpoint。确定性超限不会因重试而恢复，因此必须在所有
# embedding 入口汇合的这一层统一截断。
#
# 截断而不是抛错：检索 query 的语义主要在前部，截尾比整条检索失败好得多；
# 写入侧同理 —— 一条事实存不进去比存一个截断版本更糟。
#
# 留 64 token 余量：我们用 cl100k_base 估算，服务端 tokenizer 未必同款，
# 贴着 8192 切会偶发性再撞 400。
_EMBED_MAX_TOKENS = 8192 - 64

# tiktoken 装了就按 token 精确切，没装则退回字符估算。1 token ≈ 4 字符是英文的
# 经验值，中文更接近 1:1，所以取 2 作为保守系数 —— 宁可多截也不要再 400。
_CHARS_PER_TOKEN_FALLBACK = 2

# 本进程内的截断计数，用于量化输入被截断的频率与规模。
_TRUNCATION_STATS: Dict[str, int] = {"calls": 0, "truncated": 0, "tokens_dropped": 0}


def embed_truncation_stats() -> Dict[str, int]:
    """本进程内 embedding 截断的累计计数（calls / truncated / tokens_dropped）。"""
    return dict(_TRUNCATION_STATS)


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))
    except Exception:
        return len(text) // _CHARS_PER_TOKEN_FALLBACK + 1


def _truncate_for_embedding(text: str, limit: int = _EMBED_MAX_TOKENS) -> str:
    """把单条文本截到 embedding 能接受的长度；未超限则原样返回。

    优先按 token 精确切（tiktoken 可用时 decode 回字符串，边界准确），否则按
    保守的字符估算切。两条路径都只截尾、不改前部。
    """
    _TRUNCATION_STATS["calls"] += 1
    if not text:
        return text
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        # Input is ordinary user text, not a tokenizer control-token stream.
        ids = enc.encode(text, disallowed_special=())
        if len(ids) <= limit:
            return text
        dropped = len(ids) - limit
        out = enc.decode(ids[:limit])
    except Exception:
        est = len(text) // _CHARS_PER_TOKEN_FALLBACK + 1
        if est <= limit:
            return text
        keep = limit * _CHARS_PER_TOKEN_FALLBACK
        dropped = est - limit
        out = text[:keep]
    _TRUNCATION_STATS["truncated"] += 1
    _TRUNCATION_STATS["tokens_dropped"] += dropped
    logger.warning(
        f"[embed] input truncated to {limit} tokens "
        f"(dropped ~{dropped}; total truncated={_TRUNCATION_STATS['truncated']})"
    )
    return out


# Per-call endpoint override (contextvar → 并发安全，跨 asyncio task 传播)。
# 只覆盖「访问端点」相关字段（base_url / api_key / extra_headers / timeout），
# **不覆盖 model / embedding_dims / extra_body**——这些决定向量本身，改了会与
# 已入库向量不兼容。典型用途：业务方在 App 层解析出 Venus 直连 ip:port 后，
# 用同一个 embedding 模型换一个更快的接入端点。为空则用 EmbedService 自身配置。
_embed_endpoint_override: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "hy_embed_endpoint_override", default=None
)


@contextmanager
def embed_endpoint_override(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
):
    """在此上下文内，所有 EmbedService 的 embedding HTTP 请求改用给定端点。

    只传入的字段生效（None 表示沿用 EmbedService 自身配置）。model / dims 永不改动。
    以 contextvar 实现:进入时 set、退出时 reset,天然并发安全,且会随 await / 新建
    task 传播到 writer / reader 内部的 embed 调用,无需改动各 embed 调用点。
    """
    payload: Dict[str, Any] = {}
    if base_url is not None:
        payload["base_url"] = base_url
    if api_key is not None:
        payload["api_key"] = api_key
    if extra_headers is not None:
        payload["extra_headers"] = extra_headers
    if timeout is not None:
        payload["timeout"] = timeout
    token = _embed_endpoint_override.set(payload or None)
    try:
        yield
    finally:
        _embed_endpoint_override.reset(token)

_SENSITIVE_HEADER_KEYS = frozenset({
    "authorization", "set-cookie", "cookie", "api-key", "x-api-key",
    "x-auth-token", "proxy-authorization",
})


def _openai_version() -> str:
    """openai SDK version string (best-effort, for debug logs)."""
    try:
        import openai
        return getattr(openai, "__version__", "unknown")
    except Exception:
        return "unknown"


def _redact_headers(headers: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip/redact auth-related headers before logging."""
    if not headers:
        return headers
    out: Dict[str, Any] = {}
    for k, v in headers.items():
        if str(k).lower() in _SENSITIVE_HEADER_KEYS:
            out[k] = "***"
        else:
            out[k] = v
    return out


def _embeddings_api_url(base_url: str) -> str:
    """OpenAI SDK posts to ``{base_url}/embeddings`` (base_url usually ends with /v1)."""
    base = (base_url or "").rstrip("/")
    if not base:
        return "/embeddings"
    return f"{base}/embeddings"


@dataclass
class EmbedServiceConfig:
    """
    向量化服务配置
    """
    provider: str = "openai"
    model: str = "text-embedding-ada-002"
    api_key: str = ""
    base_url: str = ""
    embedding_dims: int = 1536
    batch_size: int = 100
    enable_cache: bool = True
    cache_size: int = 10000
    max_retries: int = 5
    retry_delay: float = 3.0
    timeout: int = 60
    # 写路径攒批队列参数
    # 语义：同一生效端点内，每 window 收一次口，把窗口内
    # 攒到的请求**一次 HTTP 批量**发出去；攒满 batch_size 则不等窗口立刻发。
    # window 取 100ms（不是 1000ms）：窗口是净增延迟，攒批只为合并请求、不该让
    # 用户多等一秒。线上曾因 1000ms 窗口 + 逐条串行发送，出现 queue_wait 6.8s。
    queue_batch_size: int = 8              # 攒满多少条立即发送（上限，不是必须攒够）
    queue_batch_window_ms: float = 100.0   # 收口窗口；到点即发，有多少发多少
    extra_headers: Optional[Dict[str, str]] = None
    extra_body: Optional[Dict[str, Any]] = None
    send_dimensions: bool = True


class EmbedService:
    """
    向量化服务
    
    提供文本到向量的转换能力。
    - embed()：直接发送，适合 search 路径（低延迟优先）
    - embed_queued()：通过攒批队列发送，适合 write 路径（减少并发数，避免打爆服务）
    """
    
    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig.from_env()
        self._embed_config = self._build_config()
        
        # 缓存
        # LRU rather than a fill-once dict. The old cache stopped accepting new
        # entries after 10k unique texts, so long eval runs lost all locality:
        # a vector computed during dedup/reconcile was requested again at store.
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._cache_lock = threading.Lock()
        
        # 统计
        self._embed_count = 0
        self._cache_hits = 0

        # 写路径攒批队列（懒初始化，首次调用 embed_queued 时创建）。
        #
        # **按端点分桶**：多个请求共享同一个 EmbedService 且使用 per-call endpoint
        # override 时，队列必须按生效端点隔离，否则：
        #   1) 正确性：一批混入不同端点，_send_batch 只跑在其中一个
        #      embed_endpoint_override contextvar 下 → 其余文本被发到**错误的
        #      端点**（用错 base_url/api_key）；
        #   2) 性能：一个慢端点拖住其他端点；
        #   3) 观测：batch_size / queue_depth 跨端点混算，失真。
        # 分桶 key 使用当前生效的 (base_url, api_key)。
        self._queue_locks: Dict[tuple, asyncio.Lock] = {}
        self._queue_pending: Dict[tuple, List[tuple]] = {}   # bucket -> [(text, Future, enter_at, lock_wait_ms, depth)]
        self._queue_flush_tasks: Dict[tuple, Optional[asyncio.Task]] = {}

        # Last embed response metadata (non-sensitive headers + body metadata)
        self._last_embed_meta: Dict[str, Any] = {}

        # AsyncOpenAI client 缓存:按 (loop_id, base_url, api_key, timeout) 复用。
        # 构造 AsyncOpenAI 会急切建 SSLContext + httpx 连接池(几十 ms),每次 embed 新建
        # 是主要开销来源(client_build_ms)。复用后仅首个请求付建连成本,后续走连接池。
        # key 带 loop_id:httpx AsyncClient 绑定 event loop,SDK 多 loop 线程(write/search)
        # 不能跨 loop 复用;带端点:per-call override 会换 base_url/api_key。
        # 重试(可重试错误)时对该 key 逐出重建,避免复用卡死连接。
        self._client_cache: Dict[tuple, Any] = {}

        logger.info(f"EmbedService initialized, model={self._embed_config.model}")
    
    def _build_config(self) -> EmbedServiceConfig:
        """从配置构建向量化配置"""
        embedder = self.config.embedder
        import os

        return EmbedServiceConfig(
            provider=embedder.provider,
            model=embedder.model,
            api_key=embedder.api_key,
            base_url=embedder.base_url,
            embedding_dims=embedder.embedding_dims,
            send_dimensions=embedder.send_dimensions,
            max_retries=embedder.max_retries if embedder.max_retries is not None else 5,
            retry_delay=embedder.retry_delay if embedder.retry_delay is not None else 3.0,
            timeout=embedder.timeout if embedder.timeout is not None else 60,
            queue_batch_size=int(os.getenv("MEMORY_EMBED_BATCH_SIZE", "8")),
            queue_batch_window_ms=float(os.getenv("MEMORY_EMBED_BATCH_WINDOW_MS", "100")),
            extra_headers=embedder.extra_headers,
            extra_body=embedder.extra_body,
        )
    
    def _get_cache_key(self, text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(text.encode()).hexdigest()

    def _cache_get(self, cache_key: str) -> Optional[List[float]]:
        with self._cache_lock:
            vector = self._cache.get(cache_key)
            if vector is not None:
                self._cache.move_to_end(cache_key)
            return vector

    def _cache_put(self, cache_key: str, vector: List[float]) -> None:
        if self._embed_config.cache_size <= 0:
            return
        with self._cache_lock:
            self._cache[cache_key] = vector
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._embed_config.cache_size:
                self._cache.popitem(last=False)

    def _endpoint_awareness(self) -> Dict[str, Any]:
        """当前生效的 provider / endpoint 感知(尊重 per-call contextvar 覆盖)。

        供缓存命中路径复用——缓存命中不发 HTTP,拿不到 _embed_openai 里算的那份 meta,
        但 provider/endpoint 是可确定的(取配置 + 当前 override),补进 meta 让
        S1_EMBED / DEDUP / READ_EMBED_QUERY 在缓存命中时也带端点感知。
        """
        _ov = _embed_endpoint_override.get()
        base_url = self._embed_config.base_url
        if _ov and _ov.get("base_url") is not None:
            base_url = _ov["base_url"]
        return {
            "provider": self._embed_config.provider,
            "endpoint": _embeddings_api_url(base_url),
            "endpoint_overridden": bool(_ov),
        }

    def _cache_hit_meta(self) -> Dict[str, Any]:
        """缓存命中的 meta:cache_hit=True + 端点感知。"""
        meta = {"cache_hit": True}
        meta.update(self._endpoint_awareness())
        return meta

    def _client_cache_key(self, base_url: Optional[str], api_key: Optional[str], timeout: int) -> tuple:
        """client 缓存 key:绑定当前 event loop + 端点参数。"""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        return (loop_id, base_url or "", api_key or "", timeout)

    def _get_client(self, base_url: Optional[str], api_key: Optional[str], timeout: int):
        """获取(或懒建)复用的 AsyncOpenAI client。同 (loop, 端点) 命中缓存,零建连开销。

        OpenAI SDK 自带默认重试（当前版本默认 2 次），而本类已经有一层显式、可观测的
        重试循环。若不关闭 SDK 内层重试，一次配置为 3 秒的外层 attempt 实际可能发出
        3 次 HTTP 请求，随后 HY Memory 再执行下一次 attempt，形成嵌套重试和延迟放大。
        这里只保留本类的重试策略，确保 attempts、日志和耗时预算一一对应。
        """
        from openai import AsyncOpenAI

        key = self._client_cache_key(base_url, api_key, timeout)
        client = self._client_cache.get(key)
        if client is None:
            client_options = {
                "api_key": api_key,
                "base_url": base_url or None,
                "timeout": timeout,
                "max_retries": 0,
            }
            transport_factory = getattr(self.config, "http_transport_factory", None)
            if callable(transport_factory):
                import httpx

                client_options["http_client"] = httpx.AsyncClient(
                    transport=transport_factory()
                )
            client = AsyncOpenAI(
                **client_options,
            )
            self._client_cache[key] = client
        return client

    def _evict_client(self, base_url: Optional[str], api_key: Optional[str], timeout: int) -> None:
        """逐出并尽力关闭指定端点的 client(重试前调用,避免复用可能卡死的连接)。"""
        key = self._client_cache_key(base_url, api_key, timeout)
        client = self._client_cache.pop(key, None)
        if client is not None:
            try:
                _close = getattr(client, "close", None)
                if _close is not None:
                    _res = _close()
                    if asyncio.iscoroutine(_res):
                        asyncio.ensure_future(_res)  # fire-and-forget,不阻塞重试
            except Exception:
                pass

    # ----------------------------------------------------------------
    # 写路径攒批队列
    # ----------------------------------------------------------------

    async def embed_queued(self, text: str, use_cache: bool = True) -> List[float]:
        """
        写路径向量化：通过攒批队列将多个并发请求合并成一次 batch 调用。
        适合 write 路径（高并发写入时避免大量并发 embed 请求）。
        search 路径请用 embed()。
        """
        vector, _meta = await self.embed_queued_with_meta(text, use_cache=use_cache)
        return vector

    async def embed_queued_with_meta(
        self, text: str, use_cache: bool = True
    ) -> Tuple[List[float], Dict[str, Any]]:
        """写路径向量化，同时返回 provider metadata（非 vector payload）。

        攒批语义：同一生效端点内，每 queue_batch_window_ms 收一次口，
        把窗口内攒到的请求**一次 HTTP 批量**发出（有多少发多少）；攒满
        queue_batch_size 则不等窗口立刻发。跨端点互不干扰（见 __init__ 分桶说明）。
        """
        # 命中缓存直接返回，不进队列
        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            cached = self._cache_get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached, self._cache_hit_meta()

        # 按当前生效端点定位请求桶；锁懒初始化（必须在 event loop 中创建）
        bucket = self._queue_bucket_key()
        lock = self._queue_locks.get(bucket)
        if lock is None:
            lock = asyncio.Lock()
            self._queue_locks[bucket] = lock

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        enter_at = time.perf_counter()
        _t_lock_wait = time.perf_counter()

        # 锁内**只做内存操作**：入队 + 判断是否该 flush + 摘走批次。
        # HTTP 发送必须放到锁外（见 _send_batch 的说明），否则一批的网络耗时
        # 会把所有并发写入串行化。
        batch_to_send = None
        async with lock:
            lock_wait_ms = round((time.perf_counter() - _t_lock_wait) * 1000, 2)
            pending = self._queue_pending.setdefault(bucket, [])
            queue_depth_at_enqueue = len(pending) + 1
            pending.append((text, fut, enter_at, lock_wait_ms, queue_depth_at_enqueue))

            # 攒满：锁内摘走批次，锁外再发
            if len(pending) >= self._embed_config.queue_batch_size:
                batch_to_send = self._detach_batch(bucket)
            else:
                # 否则启动该桶的定时 flush 任务（若未在运行）
                task = self._queue_flush_tasks.get(bucket)
                if task is None or task.done():
                    self._queue_flush_tasks[bucket] = asyncio.ensure_future(
                        self._delayed_flush(
                            self._embed_config.queue_batch_window_ms / 1000.0,
                            bucket,
                        )
                    )

        # ── 锁已释放 ── 此时其他协程可以继续入队，不被本批的 HTTP 阻塞
        if batch_to_send is not None:
            await self._send_batch(batch_to_send)

        result = await fut
        if isinstance(result, tuple) and len(result) == 2:
            vector, meta = result
        else:
            vector, meta = result, {}

        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            self._cache_put(cache_key, vector)

        self._embed_count += 1
        out_meta = dict(meta or {})
        out_meta["cache_hit"] = False
        return vector, out_meta

    async def _delayed_flush(self, delay: float, bucket: tuple) -> None:
        """窗口到点后 flush 指定端点桶（攒不满 batch_size 时的收口机制）。

        窗口默认 100ms：到点即发，有多少发多少。它是净增延迟，不宜设大——
        线上曾用 1000ms 窗口叠加逐条串行发送，出现 queue_wait 6.8s。
        """
        await asyncio.sleep(delay)
        lock = self._queue_locks.get(bucket)
        if lock is None:
            return
        # 同样遵守「锁内摘批次、锁外发 HTTP」
        async with lock:
            batch = self._detach_batch(bucket)
            # 关键：在 HTTP 开始前释放该桶的 timer 身份。否则新请求在本批 HTTP
            # 进行期间入队时会看到旧 task 仍未 done，因而不会启动下一次 timer；
            # 未攒满 batch_size 的请求会无限等待，直到偶然有后续流量把它推满。
            current = asyncio.current_task()
            if self._queue_flush_tasks.get(bucket) is current:
                self._queue_flush_tasks[bucket] = None
        if batch is not None:
            await self._send_batch(batch)

    def _queue_bucket_key(self) -> tuple:
        """当前生效端点 → 攒批桶 key。

        取当前 contextvar 里的 per-call override，无 override 时落到 client 自身配置。
        这样共享 client 的多个端点各自成桶，既不会把不同
        端点的文本混进一次批量请求（正确性），也不会互相拖累（性能）。
        """
        _ov = _embed_endpoint_override.get()
        base_url = self._embed_config.base_url
        api_key = self._embed_config.api_key
        if _ov:
            if _ov.get("base_url") is not None:
                base_url = _ov["base_url"]
            if _ov.get("api_key") is not None:
                api_key = _ov["api_key"]
        return (base_url or "", api_key or "")

    def _detach_batch(self, bucket: tuple) -> Optional[List[tuple]]:
        """把指定桶的 pending 队列整批摘走并清空；空队列返回 None。

        **必须在该桶的锁内调用**，且本函数是纯内存操作、不含 await——
        这是"锁内只做内存操作"契约的一半，另一半是 _send_batch 必须在锁外调。
        """
        pending = self._queue_pending.get(bucket)
        if not pending:
            return None
        batch = pending[:]
        pending.clear()
        return batch

    async def _send_batch(self, batch: List[tuple]) -> None:
        """把一个已摘出的批次发出去并回填各自的 future。

        **必须在锁外调用。** 早期实现把这段（含 HTTP）放在锁内，导致一批的网络
        耗时把所有并发写入串行化：线上实测单条 embed 的 lock_wait_ms 达 34.7s、
        S1_EMBED step 报 48s，而真实 HTTP 仅 1.78s。现在锁只覆盖入队/摘批次这几行
        内存操作，锁竞争与网络耗时彻底解耦。

        批内一次 HTTP 带走全部文本（见 _embed_texts_with_meta）——批内不再逐条串行。
        """
        if not batch:
            return

        flush_started_at = time.perf_counter()
        texts = [item[0] for item in batch]
        futures = [item[1] for item in batch]
        enter_times = [item[2] if len(item) > 2 else flush_started_at for item in batch]
        lock_waits = [item[3] if len(item) > 3 else 0.0 for item in batch]
        queue_depths = [item[4] if len(item) > 4 else len(batch) for item in batch]
        batch_size = len(texts)
        # 同一个攒批窗口里经常出现重复 tag（如多条 memory 都带 preferences）。
        # provider 只需计算一次，再按原位置 fan-out，避免重复 token 和队列压力。
        unique_texts = list(dict.fromkeys(texts))

        logger.debug(f"[embed_queued] flushing batch of {batch_size}")
        try:
            unique_vectors, unique_metas = await self._embed_texts_with_meta(unique_texts)
            batch_flush_ms = round((time.perf_counter() - flush_started_at) * 1000, 2)

            # 批量模式下 provider 必须按 input 条数返回同样多的向量。数量不符时
            # 不能用 zip 静默截断——那会让多出来的 future **永久悬挂**，调用方
            # （write 链路）一直卡住直到上层超时。这里显式判定并给剩余 future
            # 设异常，把故障立刻暴露出来。
            if len(unique_vectors) != len(unique_texts):
                raise RuntimeError(
                    f"embed batch size mismatch: requested {len(unique_texts)} unique texts, "
                    f"provider returned {len(unique_vectors)} vectors"
                )

            by_text = {
                text: (unique_vectors[i], unique_metas[i])
                for i, text in enumerate(unique_texts)
            }

            for idx, (text, fut) in enumerate(zip(texts, futures)):
                vec, meta = by_text[text]
                enriched_meta = dict(meta or {})
                enriched_meta.update({
                    "batch_size": batch_size,
                    "batch_unique_count": len(unique_texts),
                    "position_in_batch": idx + 1,
                    "queue_depth": queue_depths[idx],
                    "queue_wait_ms": round((flush_started_at - enter_times[idx]) * 1000, 2),
                    "lock_wait_ms": lock_waits[idx],
                    "batch_flush_ms": batch_flush_ms,
                    "queue_batch_size_config": self._embed_config.queue_batch_size,
                    "queue_batch_window_ms": self._embed_config.queue_batch_window_ms,
                })
                if not fut.done():
                    fut.set_result((vec, enriched_meta))
        except Exception as e:
            logger.error(f"[embed_queued] batch failed: {e}")
            for fut in futures:
                if not fut.done():
                    fut.set_exception(e)
        finally:
            # 兜底：无论成功/异常，绝不允许任何 future 悬挂——否则 write 链路会
            # 一直 await 下去。正常路径上这里不会命中。
            for fut in futures:
                if not fut.done():
                    fut.set_exception(
                        RuntimeError("embed batch future left unresolved")
                    )
    
    async def embed(
        self,
        text: str,
        use_cache: bool = True,
    ) -> List[float]:
        """
        向量化单条文本
        
        Args:
            text: 文本内容
            use_cache: 是否使用缓存
        
        Returns:
            向量列表
        """
        # 检查缓存
        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            cached = self._cache_get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                logger.debug(f"[embed] cache hit (total hits={self._cache_hits})")
                return cached

        # 调用向量化
        vectors = await self._embed_texts([text])
        vector = vectors[0]
        
        # 更新缓存
        if use_cache and self._embed_config.enable_cache:
            self._cache_put(cache_key, vector)
        
        self._embed_count += 1
        return vector
    
    async def embed_batch(
        self,
        texts: List[str],
        use_cache: bool = True,
    ) -> List[List[float]]:
        """
        批量向量化
        
        Args:
            texts: 文本列表
            use_cache: 是否使用缓存
        
        Returns:
            向量列表
        """
        vectors, _metas = await self.embed_batch_with_meta(texts, use_cache=use_cache)
        return vectors

    async def embed_batch_with_meta(
        self,
        texts: List[str],
        use_cache: bool = True,
    ) -> Tuple[List[List[float]], List[Dict[str, Any]]]:
        """批量向量化，同时返回每条的 provider metadata（非 vector）。"""
        if not texts:
            return [], []

        results: List[Any] = [None] * len(texts)
        metas: List[Dict[str, Any]] = [{} for _ in texts]
        texts_to_embed = []
        indices_to_embed = []

        for i, text in enumerate(texts):
            if use_cache and self._embed_config.enable_cache:
                cache_key = self._get_cache_key(text)
                cached = self._cache_get(cache_key)
                if cached is not None:
                    results[i] = cached
                    metas[i] = self._cache_hit_meta()
                    self._cache_hits += 1
                    continue

            texts_to_embed.append(text)
            indices_to_embed.append(i)

        if texts_to_embed:
            vectors, batch_metas = await self._embed_texts_with_meta(texts_to_embed)

            for j, vector in enumerate(vectors):
                idx = indices_to_embed[j]
                results[idx] = vector
                m = dict(batch_metas[j] if j < len(batch_metas) else {})
                m["cache_hit"] = False
                metas[idx] = m

                if use_cache and self._embed_config.enable_cache:
                    cache_key = self._get_cache_key(texts_to_embed[j])
                    self._cache_put(cache_key, vector)

            self._embed_count += len(texts_to_embed)

        return results, metas
    
    async def _embed_texts_with_meta(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], List[Dict[str, Any]]]:
        """实际的向量化调用：**一次 HTTP 带走整批**；返回 (vectors, metas)。

        早期实现是 `for text in texts: await _embed_openai([text])` 逐条串行，
        原意是"避免 batch size 限制"，但代价极大：线上一批 8 条 × ~5.6s 串行
        累计 batch_flush_ms=44.7s，把攒批的收益全赔进去还倒亏
        （S1_EMBED 报 51.7s，其中真实单条 HTTP 仅 2.8s）。

        OpenAI embedding 协议原生支持 `input` 传数组，一次往返即可拿回全部向量，
        故直接批量发送——耗时从 Σ(每条) 降到 1 次往返。

        metas 的处理：批量只有一份 response meta（http_status / traceId / usage
        等是整个请求级的），这里复制给每条并加 `batch_mode`/`batch_index`
        标记来源，保持 per-item meta 的既有契约不破（调用方仍按下标取）。

        截断在这里做（发送前逐条过 `_truncate_for_embedding`）：所有 embedding
        路径（embed / embed_batch / embed_queued / *_with_meta）最终都落到这个
        方法 —— 放在各入口会漏。理由与阈值见模块头部 `_EMBED_MAX_TOKENS`。
        """
        if not texts:
            return [], []

        safe_texts = [_truncate_for_embedding(t) for t in texts]
        vectors, meta = await self._embed_openai(safe_texts)
        base_meta = dict(meta or {})
        base_meta["batch_mode"] = "single_request"
        base_meta["batch_input_count"] = len(safe_texts)

        all_metas: List[Dict[str, Any]] = []
        for idx in range(len(safe_texts)):
            m = dict(base_meta)
            m["batch_index"] = idx
            all_metas.append(m)

        self._last_embed_meta = all_metas[-1] if all_metas else {}
        return vectors, all_metas

    async def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        """实际的向量化调用（统一走 OpenAI 兼容协议），逐条调用避免 batch size 限制"""
        vectors, _metas = await self._embed_texts_with_meta(texts)
        return vectors
    
    def _log_embed_call(
        self,
        *,
        status: str,
        api_url: str,
        request_payload: Dict[str, Any],
        response_payload: Dict[str, Any],
    ) -> None:
        """
        打一条 INFO 级 embedding 请求日志（含 request_id / provider / model / api / 入参 / 出参）。

        出参只含 vector 维度，不含向量数值。入参的 input 是待向量化的原文
        （对话 / 记忆 / 查询），INFO 只记每条的字符数（input_chars），原文仅在
        DEBUG 单独输出。request_id 同时写入 message，
        并依赖 log formatter 的 %(request_id)s（setup_logging 后生效）。
        """
        texts = request_payload.get("input")
        info_request = {k: v for k, v in request_payload.items() if k != "input"}
        if isinstance(texts, list):
            info_request["input_chars"] = [len(str(t)) for t in texts]
        payload = {
            "request_id": get_request_id(),
            "status": status,
            "provider": self._embed_config.provider,
            "model": self._embed_config.model,
            "api": api_url,
            "request": info_request,
            "response": response_payload,
        }
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            body = str(payload)
        logger.info(f"[embed] {body}")
        if texts is not None and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[embed] input: {json.dumps(texts, ensure_ascii=False, default=str)}"
            )

    async def _embed_openai(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """
        使用 OpenAI 兼容接口向量化

        支持所有 OpenAI 兼容的 embedding 服务：
        - OpenAI 官方
        - 阿里云 DashScope (text-embedding-v3)
        - DeepSeek
        - 本地 vLLM / Ollama 等

        请求参数：model + input (+ dimensions) + extra_headers + extra_body。
        `embedding_dims` 为空或 0，或 `send_dimensions=False` 时不发送 dimensions。
        配置的维度仍用于校验响应，避免将不兼容的向量写入 collection。

        Returns:
            (vectors, meta)：meta 为 response headers + body 非 vector 字段
            （http_status / model / usage / attempts 等）。同时写入
            self._last_embed_meta（并发调用下会竞态，调用方应使用返回的 meta）。
        """
        if importlib.util.find_spec("openai") is None:
            raise ImportError("openai is required. Install with: pip install openai")

        # 仅对以下"疑似网络/服务端抖动"的错误做重试；业务错（400/401 等）立即抛。
        # 使用字符串匹配是为了避免额外 import openai 的具体异常类型（不同版本命名有变）。
        _RETRYABLE = (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            # HTTP 200 with an empty `data` list is still "no usable response".
            # The gateway has returned this transiently for longer episode texts;
            # treating it as a permanent ValueError wastes the preceding summary
            # generation and leaves the session without L3.
            ValueError,
            # Some OpenAI-compatible gateways return an empty/non-JSON body for
            # a transient upstream failure.
            # openai surfaces that as JSONDecodeError without an HTTP status.
            JSONDecodeError,
        )
        # 网关抖动按 HTTP 状态码判，不靠 message 关键字。
        #
        # 状态码取自异常实例（APIStatusError 实例有 .status_code，类上没有，所以
        # 用 getattr 而不是 isinstance，也就不必 import 具体异常类）。
        #   5xx        服务端/网关侧，重试有意义（502/503/504 是这次实测到的）
        #   408 / 429  请求超时与限流，同样是重试语义
        #   其余 4xx   业务错（400 入参、401 鉴权），重试只是放大故障，立即抛
        _RETRYABLE_STATUS = {408, 429}

        kwargs: Dict[str, Any] = {
            "model": self._embed_config.model,
            "input": texts,
        }
        if self._embed_config.embedding_dims and self._embed_config.send_dimensions:
            kwargs["dimensions"] = self._embed_config.embedding_dims

        # ── Per-call 端点覆盖（contextvar）──────────────────────────
        # 只换访问端点（base_url/api_key/extra_headers/timeout），model/dims 不变。
        # 覆盖不落 self._embed_config,只作用于本次请求,避免污染共享实例。
        _ov = _embed_endpoint_override.get()
        _eff_base_url = self._embed_config.base_url
        _eff_api_key = self._embed_config.api_key
        _eff_timeout = self._embed_config.timeout or 15
        _eff_extra_headers = self._embed_config.extra_headers
        if _ov:
            if _ov.get("base_url") is not None:
                _eff_base_url = _ov["base_url"]
            if _ov.get("api_key") is not None:
                _eff_api_key = _ov["api_key"]
            if _ov.get("timeout") is not None:
                _eff_timeout = _ov["timeout"]
            if _ov.get("extra_headers") is not None:
                _eff_extra_headers = _ov["extra_headers"]

        if _eff_extra_headers:
            kwargs["extra_headers"] = _eff_extra_headers
        extra_body = dict(self._embed_config.extra_body or {})
        # extra_body is merged after named arguments by the OpenAI client. Keep
        # it from restoring dimensions when omitted, without mutating caller data.
        if not self._embed_config.send_dimensions:
            extra_body.pop("dimensions", None)
        if extra_body:
            kwargs["extra_body"] = extra_body

        api_url = _embeddings_api_url(_eff_base_url)
        # 入参全量落日志（不含 api_key）；header 中鉴权字段脱敏
        request_payload: Dict[str, Any] = {
            "model": kwargs.get("model"),
            "input": texts,
            "input_count": len(texts),
            "dimensions": kwargs.get("dimensions"),
            "extra_body": extra_body or None,
            "extra_headers": _redact_headers(_eff_extra_headers),
            "timeout": _eff_timeout,
            "endpoint_overridden": bool(_ov),
        }

        _max_retries = max(0, int(self._embed_config.max_retries or 0))
        _retry_delay = max(0.0, float(self._embed_config.retry_delay or 0.0))
        last_exc: Optional[Exception] = None

        for attempt in range(_max_retries + 1):
            # 复用 client(按 loop+端点缓存):构造 AsyncOpenAI 会急切建 SSLContext +
            # httpx 连接池(几十 ms),每次新建是 client_build_ms 的主要来源。复用后
            # 仅首个请求付建连成本,后续走连接池。client_build_ms / embed_request_ms
            # 分开打点,区分「建连开销」与「embedding 服务处理 + 网络往返」。
            # 上一 attempt 失败(可重试)时已 evict,故这里 miss 会重建一个干净 client。
            _t_client = time.perf_counter()
            client = self._get_client(_eff_base_url, _eff_api_key, _eff_timeout)
            _client_build_ms = round((time.perf_counter() - _t_client) * 1000, 2)
            try:
                # Use with_raw_response to capture HTTP headers (provider traceId etc.)
                # embed_request_ms 只圈住这一次 HTTP await：建连（本 client 首用即在此
                # 握手）+ 请求发送 + 服务端处理 + 响应接收。它已排除上游 loop 调度、
                # cache 查找、asyncio.wait_for 包装等 SDK 外围耗时。
                _t_req = time.perf_counter()
                async with embedding_slot():
                    raw = await client.embeddings.with_raw_response.create(**kwargs)
                _embed_request_ms = round((time.perf_counter() - _t_req) * 1000, 2)
                response = raw.parse()

                # Store metadata for trace logging (headers + body fields except vectors)
                resp_headers = {
                    k: v for k, v in raw.headers.items()
                    if k.lower() not in _SENSITIVE_HEADER_KEYS
                }
                # 按 index 排序后再取向量：OpenAI embedding 协议规定每个 item 带
                # index（对应 input 数组下标），但**不保证 data 数组本身有序**。
                # 批量请求（一次发多条）下若直接按 data 顺序取，可能把向量配错文本。
                # 单条请求时排序是 no-op，无额外开销。
                _data = sorted(
                    response.data,
                    key=lambda it: getattr(it, "index", 0) or 0,
                )
                vectors = [item.embedding for item in _data]
                if len(vectors) != len(texts) or any(not vector for vector in vectors):
                    raise ValueError(
                        "No embedding data received"
                        if not vectors else
                        f"Embedding count mismatch: expected={len(texts)} "
                        f"got={len(vectors)}"
                    )
                dims = [len(v) for v in vectors]
                # Omitting the request parameter must not disable collection-width
                # validation. A fixed-width model mismatch cannot heal on retry.
                expected_dims = self._embed_config.embedding_dims
                if expected_dims and any(dim != expected_dims for dim in dims):
                    raise EmbeddingDimensionMismatchError(
                        f"Embedding dimension mismatch: expected={expected_dims} got={dims}"
                    )
                meta = {
                    "http_status": raw.status_code,
                    "response_headers": resp_headers,
                    "model": response.model,
                    "usage": response.usage.model_dump() if response.usage else None,
                    "attempts": attempt + 1,
                    # 客户端视角自测耗时（SDK 不感知对端服务，纯 HTTP 口径）：
                    "embed_request_ms": _embed_request_ms,   # 这次 embedding HTTP 往返（含建连+服务端处理）
                    "client_build_ms": _client_build_ms,     # 新建 AsyncOpenAI 的开销
                    # 这次实际用的 provider / 端点感知（流进 S1_EMBED / DEDUP / search embed_meta，
                    # 供 inspector 判断该条 embed 走的是网关还是 per-call override 直连）：
                    "provider": self._embed_config.provider,
                    "endpoint": api_url,                       # 实际请求地址（override 后即直连 ip:port）
                    "endpoint_overridden": bool(_ov),          # 是否走了 per-call 端点覆盖
                }
                self._last_embed_meta = meta
                # INFO：完整 embed 请求链路（provider/model/api/入参/出参dims）
                self._log_embed_call(
                    status="ok",
                    api_url=api_url,
                    request_payload=request_payload,
                    response_payload={
                        "http_status": raw.status_code,
                        "model": response.model,
                        "usage": meta.get("usage"),
                        "attempts": attempt + 1,
                        "vector_count": len(vectors),
                        "vector_dims": dims,
                        "embed_request_ms": _embed_request_ms,
                        "client_build_ms": _client_build_ms,
                        # 只打维度，不打向量本体
                        "response_headers": resp_headers,
                    },
                )
                return vectors, meta
            except Exception as e:
                last_exc = e
                _err_name = type(e).__name__
                _err_msg = str(e)
                # 判断是否可重试：HTTP 状态码 或 显式异常类型 或 消息关键字。
                # 状态码优先——它是三者里唯一不受 message 措辞影响的判据。
                _status = getattr(e, "status_code", None)
                _retryable = (
                    not isinstance(e, EmbeddingDimensionMismatchError)
                    and (
                        (isinstance(_status, int)
                         and (_status >= 500 or _status in _RETRYABLE_STATUS))
                        or isinstance(e, _RETRYABLE)
                        or any(
                            kw in _err_msg.lower()
                            for kw in ("timeout", "timed out", "connection", "read operation")
                        )
                    )
                )
                meta = {
                    "error": _err_name,
                    "error_msg": _err_msg[:300],
                    "attempts": attempt + 1,
                    "retryable": _retryable,
                    # 状态码单独落一列：之前排查 504 只能去 error_msg 里捞 HTML，
                    # 而 error 字段一律是 InternalServerError，分不出 502/503/504。
                    "http_status": _status if isinstance(_status, int) else None,
                    # 端点感知：失败时同样带上,便于定位是哪个端点(网关/直连)出的问题
                    "provider": self._embed_config.provider,
                    "endpoint": api_url,
                    "endpoint_overridden": bool(_ov),
                }
                self._last_embed_meta = meta
                if _retryable and attempt < _max_retries:
                    # 逐出可能卡死的 client,下个 attempt 重建干净连接(恢复原防御语义)
                    self._evict_client(_eff_base_url, _eff_api_key, _eff_timeout)
                    logger.warning(
                        f"[embed] attempt {attempt + 1}/{_max_retries + 1} failed "
                        f"({_err_name}): {_err_msg[:200]}; retry in {_retry_delay}s"
                    )
                    if _retry_delay > 0:
                        await asyncio.sleep(_retry_delay)
                    continue
                self._log_embed_call(
                    status="error",
                    api_url=api_url,
                    request_payload=request_payload,
                    response_payload={
                        "attempts": attempt + 1,
                        "error": _err_name,
                        "error_msg": _err_msg[:500],
                        "retryable": _retryable,
                        "http_status": _status if isinstance(_status, int) else None,
                    },
                )
                raise

        # 理论不可达；兜底重抛
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("embed_openai: unreachable state")

    async def embed_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple:
        """
        向量化单条文本，同时返回 provider metadata（含 Venus traceId 等）。

        并发安全：meta 从 _embed_openai 返回值取得，不依赖 self._last_embed_meta。

        Returns:
            (vector, meta_dict)
            meta_dict 包含 http_status, response_headers, model, usage 等。
            如果命中缓存，meta_dict 为 {"cache_hit": True}。
        """
        # Check cache
        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            cached = self._cache_get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached, self._cache_hit_meta()

        vectors, meta = await self._embed_openai([_truncate_for_embedding(text)])
        vector = vectors[0]

        # Update cache
        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            self._cache_put(cache_key, vector)

        self._embed_count += 1
        out_meta = dict(meta or {})
        out_meta["cache_hit"] = False
        return vector, out_meta


    async def embed_with_cache_info(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple:
        """
        向量化单条文本，返回是否命中缓存
        
        Args:
            text: 文本内容
            use_cache: 是否使用缓存
        
        Returns:
            (向量列表, 是否命中缓存)
        """
        # 检查缓存
        if use_cache and self._embed_config.enable_cache:
            cache_key = self._get_cache_key(text)
            cached = self._cache_get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached, True
        
        # 调用向量化
        vectors = await self._embed_texts([text])
        vector = vectors[0]
        
        # 更新缓存
        if use_cache and self._embed_config.enable_cache:
            self._cache_put(cache_key, vector)
        
        self._embed_count += 1
        return vector, False
    
    def get_embedding_dims(self) -> int:
        """获取向量维度"""
        return self._embed_config.embedding_dims
    
    def clear_cache(self) -> None:
        """清空缓存"""
        with self._cache_lock:
            self._cache.clear()
        logger.info("EmbedService cache cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        cache_size = self._cache_len()
        return {
            "embed_count": self._embed_count,
            "cache_hits": self._cache_hits,
            "cache_size": cache_size,
            "cache_hit_rate": (
                self._cache_hits / (self._embed_count + self._cache_hits)
                if (self._embed_count + self._cache_hits) > 0 else 0
            ),
            "model": self._embed_config.model,
            "embedding_dims": self._embed_config.embedding_dims,
        }

    def _cache_len(self) -> int:
        with self._cache_lock:
            return len(self._cache)
