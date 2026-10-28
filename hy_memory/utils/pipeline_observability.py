# -*- coding: utf-8 -*-
"""
Pipeline 可观测性：Log（文件 JSONL，默认开启，可关闭）与 Trace（SQLite pipeline_logs，可关闭）。

- Log：运维 tail/grep，每个环节即时一行，不依赖后续步骤成功与否。
- Trace：Inspector / 调试，落库 pipeline_logs（需 MEMORY_CACHE_BACKEND=sqlite 等持久化后端）。

环境变量：
  MEMORY_PIPELINE_LOG_ENABLED    — Log 写 JSONL 文件，默认 true；false 时完全不写
  MEMORY_PIPELINE_TRACE_ENABLED  — Trace 落库，默认 true；OpenClaw C 端可设 false
  MEMORY_MEMORY_OPERATIONS_ENABLED — memory_operations 审计表落库，默认 false
  MEMORY_LOG_DIR                 — Log 目录（优先）
  MEMORY_TRACE_LOG_DIR           — Log 目录（兼容旧名）

Log 与 Trace 都关闭时不包装 store_pipeline_log，而是换成立即返回 True 的
空操作：热路径不做任何计时、脱敏或后台任务，也不写库。

子目录（{log_dir}/{subdir}/{date}.log）由 PipelineLogWriter 从每条记录的
user_id 前缀（第一个 "__" 左侧）切分；无前缀回落 "default"。该前缀是
通用 namespace，可用于项目、部署或数据分桶。

写入语义（search/write 热路径）：
  store_pipeline_log 包装后对调用方 **立即返回**；JSONL / DB 落盘在后台任务完成。
  event_at_ms / step-to-step elapsed_ms 在入队前同步打点，不受后台 IO 影响。
  调用方（如 App 侧 write_trace()）若已在事件真正发生的瞬间同步捕获了
  event_at_ms 并显式传入，本模块优先复用它而不是用本地 time.time() 覆盖——
  这样即使调用经过跨线程/跨队列转发到这里已产生延迟，落库时间也仍对齐真实
  事件时刻；不传或传 0 时才回落本地当前时间。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Set

from .async_executor import run_in_executor_guarded

logger = logging.getLogger(__name__)

# JSONL 同步 open/write 放到专用线程池，避免堵 asyncio 事件循环。
_jsonl_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="pipeline-jsonl",
)

# 测试可 await drain_pipeline_log_tasks() 等待后台落盘完成。
_pending_log_tasks: Set["asyncio.Task[Any]"] = set()

_TERMINAL_STEPS = {"READ_SUMMARY", "READ_CODING_RESULT", "WRITE_TIMELINE"}
_LAST_EVENT_MAX = 4096
# step → 该 step 主要耗时的语义 key。注入的值是业务侧传进 store_pipeline_log 的
# elapsed_ms，即「该环节整体墙钟耗时」。
#
# 命名原则：只有当该 step 的耗时**必然等于**某种具体操作耗时时，才用具体语义 key
# （embed_ms / llm_ms / vdb_search_ms）；否则用中立的 step_ms。
#
# READ_SCENE_JUDGE 用 step_ms 而非 llm_ms：它只在 mode="llm" 时才真调 LLM，
# mode="default"（单 query / 功能关闭）与 "explicit"（显式指定 scene）都是
# 纯同步分支、根本不调 LLM。此前统一映射成 llm_ms 会让人把「环节耗时」误读成
# 「LLM 耗时」（线上曾出现 mode=default 却 llm_ms=445 的困惑）。真实 LLM 耗时
# 改由业务侧在三个 LLM 分支显式塞 parsed.llm_ms（见 client._async_search_impl）。
#
# S1_EMBED 用 embed_ms：writer 侧用 `datetime.now()` 精确圈住
# `embed_queued_with_meta()` 这一次调用，涵盖 入队 → 等锁 → 等攒批 → HTTP，
# 正是「用户感受到的整个 embed 环节耗时」。**告警应盯这个字段**——只看
# provider_meta.embed_request_ms（纯 HTTP）会把排队/锁竞争问题整个隐藏掉
# （线上事故：embed_request_ms 仅 1.7s，但排队使环节耗时达 48s）。
# 此前 S1_EMBED 未登记在本表中，导致业务侧算好的耗时被 _inject_op_ms 丢弃、
# parsed.embed_ms 恒为 None，只能退而用顶层 elapsed_ms（step 间隔）做告警。
_STEP_TIMING_KEY = {
    "READ_EMBED_QUERY": "embed_ms", "READ_CODING_EMBED": "embed_ms",
    "READ_RECALL_VEC": "vdb_search_ms", "READ_HIDDEN_L0_CONTEXT": "vdb_search_ms",
    "READ_RECALL_TAG": "vdb_search_ms", "READ_BM25": "vdb_search_ms",
    "READ_TAG_MATCH": "vdb_search_ms", "READ_CODING_VDB": "vdb_search_ms",
    "READ_KEYWORD_EMBED": "embed_ms",
    "READ_SCENE_JUDGE": "step_ms",
    "S1_EMBED": "embed_ms",
    "EXTRACT": "llm_ms", "RECONCILE": "llm_ms",
    "SEARCH_QUERY": "llm_ms", "SUMMARY": "llm_ms",
}


def is_pipeline_trace_enabled() -> bool:
    """是否将 pipeline step 写入 cache（SQLite pipeline_logs），供 Inspector 使用。"""
    return os.getenv("MEMORY_PIPELINE_TRACE_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
    )


def is_pipeline_log_enabled() -> bool:
    """是否把 pipeline step 写成 JSONL 文件（{log_dir}/<subdir>/<date>.log）。默认开启。"""
    return os.getenv("MEMORY_PIPELINE_LOG_ENABLED", "true").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def is_memory_operations_enabled() -> bool:
    """是否将知识库变动写入 memory_operations 审计表。默认关闭（false）。"""
    return os.getenv("MEMORY_MEMORY_OPERATIONS_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def is_llm_content_logging_enabled() -> bool:
    """Whether pipeline logs/traces may persist full LLM input and output."""
    return os.getenv("MEMORY_LOG_LLM_CONTENT", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


def redact_content(value: str) -> str:
    """Keep diagnostics without persisting the original sensitive text."""
    import hashlib

    text = str(value or "")
    if not text:
        return ""
    if text.startswith("<redacted len=") and text.endswith(">"):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"<redacted len={len(text)} sha256={digest}>"


def resolve_pipeline_log_dir() -> str:
    """
    JSONL pipeline log 根目录（MEMORY_PIPELINE_LOG_ENABLED 开启时使用，有默认值）。

    优先级: MEMORY_LOG_DIR > MEMORY_TRACE_LOG_DIR > {MEMORY_DATA_DIR}/logs/pipeline
    """
    explicit = os.getenv("MEMORY_LOG_DIR") or os.getenv("MEMORY_TRACE_LOG_DIR")
    if explicit:
        return explicit
    data_dir = os.getenv("MEMORY_DATA_DIR", os.path.join(Path.home(), ".hy_memory"))
    return os.path.join(data_dir, "logs", "pipeline")


def split_namespace_user(user_id: str) -> tuple:
    """把 SDK user_id 按第一个 "__" 拆成 (namespace, user_id)。

    - "project__u1"        -> ("project", "u1")
    - "project__u1__extra" -> ("project", "u1__extra")
    - "u1"（无 "__"）  -> ("", "u1")
    - ""               -> ("", "")
    """
    if not user_id:
        return "", ""
    if "__" in user_id:
        namespace, rest = user_id.split("__", 1)
        return namespace, rest
    return "", user_id




def _inject_op_ms(parsed_str: str, step: str, op_ms: float) -> str:
    """把本环节操作耗时按语义 key（embed_ms/llm_ms/vdb_search_ms）注入 parsed JSON。"""
    key = _STEP_TIMING_KEY.get(step)
    if not key or not op_ms:
        return parsed_str
    try:
        obj = json.loads(parsed_str) if parsed_str else {}
        if isinstance(obj, dict):
            obj[key] = round(float(op_ms), 2)
            return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        pass
    return parsed_str


def _track_task(task: "asyncio.Task[Any]") -> None:
    _pending_log_tasks.add(task)

    def _done(t: "asyncio.Task[Any]") -> None:
        _pending_log_tasks.discard(t)
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.debug(f"[pipeline-log] background task failed: {exc}")

    task.add_done_callback(_done)


async def drain_pipeline_log_tasks(timeout: float = 5.0) -> None:
    """等待所有后台 pipeline log 任务完成（仅供测试 / 优雅关闭）。"""
    if not _pending_log_tasks:
        return
    current_loop = asyncio.get_running_loop()
    pending = []
    stale = []
    for task in list(_pending_log_tasks):
        if task.done():
            stale.append(task)
            continue
        try:
            task_loop = task.get_loop()
        except Exception:
            stale.append(task)
            continue
        if task_loop is current_loop:
            pending.append(task)
        elif task_loop.is_closed():
            stale.append(task)
    for task in stale:
        _pending_log_tasks.discard(task)
    if not pending:
        return
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        pending = [task for task in pending if not task.done()]
    if pending:
        logger.warning(
            f"[pipeline-log] drain timeout after {timeout}s, "
            f"{len(_pending_log_tasks)} tasks still pending"
        )


def wrap_store_pipeline_log(
    original_store: Callable[..., Any],
    *,
    log_writer: Any,
    trace_enabled: bool,
    owner_loop: Any = None,
) -> Callable[..., Any]:
    """包装 cache.store_pipeline_log：计时同步、JSONL/DB 异步落盘，不堵主请求。

    - log_writer 为 None 时不写 JSONL（MEMORY_PIPELINE_LOG_ENABLED=false）
    - step-to-step elapsed_ms / event_at_ms 在调用瞬间打点（与业务墙钟对齐）
    - event_at_ms：调用方传入非 0 值则直接复用（App 已在自己被调用的那一刻
      同步打点，比这里更接近真实事件时刻）；传 0 / 不传才用本地 time.time()
    - JSONL 走线程池；DB Trace 走 create_task
    - 调用方 await 后立即返回 True，不因磁盘/DB 慢而拉长 search RTT
    - owner_loop：cache（如 aiomysql pool）绑定的 loop。search 走独立 loop 后，
      从 search loop 触发的 DB 落库必须派发回 owner_loop 执行，否则跨 loop 用
      aiomysql 会污染连接池，因此必须回到 cache 所属 loop。
      为 None 时退回"当前 loop"（solo/单 loop 旧行为）。
    """
    _last_event: OrderedDict[str, float] = OrderedDict()
    try:
        _trace_max_pending = max(
            1, int(os.getenv("MEMORY_PIPELINE_TRACE_MAX_PENDING", "64")),
        )
    except (TypeError, ValueError):
        _trace_max_pending = 64
    try:
        _trace_max_concurrency = max(
            1, int(os.getenv("MEMORY_PIPELINE_TRACE_MAX_CONCURRENCY", "2")),
        )
    except (TypeError, ValueError):
        _trace_max_concurrency = 2
    try:
        _trace_timeout = max(
            0.05, float(os.getenv("MEMORY_PIPELINE_TRACE_TIMEOUT_SECONDS", "1.0")),
        )
    except (TypeError, ValueError):
        _trace_timeout = 1.0
    _trace_lock = threading.Lock()
    _trace_pending = 0
    _trace_dropped = 0
    _trace_semaphores = weakref.WeakKeyDictionary()

    def _reserve_trace_slot() -> bool:
        nonlocal _trace_pending, _trace_dropped
        with _trace_lock:
            if _trace_pending >= _trace_max_pending:
                _trace_dropped += 1
                if _trace_dropped == 1 or not (_trace_dropped & (_trace_dropped - 1)):
                    logger.warning(
                        "[pipeline-log] dropping DB trace: pending=%d max=%d dropped=%d",
                        _trace_pending, _trace_max_pending, _trace_dropped,
                    )
                return False
            _trace_pending += 1
            return True

    def _trace_stats() -> dict:
        with _trace_lock:
            return {
                "pending": _trace_pending,
                "dropped": _trace_dropped,
                "max_pending": _trace_max_pending,
                "max_concurrency": _trace_max_concurrency,
                "timeout_seconds": _trace_timeout,
            }

    def _release_once(state: dict) -> None:
        nonlocal _trace_pending
        with _trace_lock:
            if state.get("released"):
                return
            state["released"] = True
            _trace_pending = max(0, _trace_pending - 1)

    async def _store_pipeline_observability(
        *,
        request_id: str = "",
        user_id: str = "",
        agent_id: str = "",
        step: str = "",
        prompt: str = "",
        response: str = "",
        parsed: str = "",
        memory_ids=None,
        elapsed_ms: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        session_id: str = "",
        event_at_ms: int = 0,
        **kwargs,
    ):
        # 调用方（如 App 层）如果已经在自己被调用的那一刻同步打过点、显式传入
        # event_at_ms，就用它——这才是事件真实发生的墙钟时刻。默认 0 时才退回
        # 用「这个 hook 被调度到、真正执行到」的 time.time()，这只是本包装函数
        # 自己被调用的时刻，调用方经过 create_task/跨线程调度后可能已经晚了。
        # 显式命名参数（而不是让它落进 **kwargs）是为了避免下面 original_store(
        # event_at_ms=event_at_ms, **extra_kwargs) 出现重复关键字参数报错。
        _now = time.time()
        event_at_ms = int(event_at_ms) if event_at_ms else int(_now * 1000)

        parsed = _inject_op_ms(parsed, step, elapsed_ms)
        if not is_llm_content_logging_enabled():
            prompt = redact_content(prompt)
            response = redact_content(response)

        _prev = _last_event.get(request_id)
        step_elapsed_ms = round((_now - _prev) * 1000, 2) if _prev is not None else 0.0
        if step in _TERMINAL_STEPS:
            _last_event.pop(request_id, None)
        else:
            _last_event[request_id] = _now
            _last_event.move_to_end(request_id)
            while len(_last_event) > _LAST_EVENT_MAX:
                _last_event.popitem(last=False)

        # 快照可变入参，避免后台任务读到后续被改掉的 list
        mem_ids = list(memory_ids) if memory_ids is not None else None
        extra_kwargs = dict(kwargs)

        async def _persist_jsonl() -> None:
            try:
                await run_in_executor_guarded(
                    _jsonl_executor,
                    log_writer.write_step,
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    step=step,
                    prompt=prompt,
                    response=response,
                    parsed=parsed,
                    memory_ids=mem_ids,
                    elapsed_ms=step_elapsed_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    event_at_ms=event_at_ms,
                )
            except Exception as e:
                logger.debug(f"[pipeline-log] JSONL write failed: {e}")

        release_state = {"released": False}

        async def _persist_trace() -> None:
            if not trace_enabled:
                return
            try:
                execution_loop = asyncio.get_running_loop()
                with _trace_lock:
                    semaphore = _trace_semaphores.get(execution_loop)
                    if semaphore is None:
                        semaphore = asyncio.Semaphore(_trace_max_concurrency)
                        _trace_semaphores[execution_loop] = semaphore

                async def _limited_store() -> None:
                    async with semaphore:
                        await original_store(
                            request_id=request_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            step=step,
                            prompt=prompt,
                            response=response,
                            parsed=parsed,
                            memory_ids=mem_ids,
                            elapsed_ms=step_elapsed_ms,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            event_at_ms=event_at_ms,
                            **extra_kwargs,
                        )

                await asyncio.wait_for(_limited_store(), timeout=_trace_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "[pipeline-log] trace store timed out after %.2fs step=%s",
                    _trace_timeout, step,
                )
            except Exception as e:
                logger.debug(f"[pipeline-log] trace store failed: {e}")
            finally:
                _release_once(release_state)

        try:
            loop = asyncio.get_running_loop()
            # JSONL 用线程池，与 loop 无亲和，留在当前 loop。
            if log_writer is not None:
                _track_task(loop.create_task(_persist_jsonl()))
            # DB trace 用 cache pool（可能 aiomysql，绑定 owner_loop）。若当前不在
            # owner_loop（如 search 走独立 loop），派发回 owner_loop 执行，避免跨 loop。
            if trace_enabled:
                if _reserve_trace_slot():
                    try:
                        if owner_loop is not None and owner_loop is not loop:
                            trace_coro = _persist_trace()
                            try:
                                future = asyncio.run_coroutine_threadsafe(
                                    trace_coro, owner_loop,
                                )
                            except Exception:
                                trace_coro.close()
                                raise

                            def _consume(f):
                                try:
                                    f.result()
                                except Exception as e:
                                    logger.debug(
                                        "[pipeline-log] cross-loop trace failed: %s", e,
                                    )
                                finally:
                                    _release_once(release_state)

                            future.add_done_callback(_consume)
                        else:
                            _track_task(loop.create_task(_persist_trace()))
                    except Exception as e:
                        _release_once(release_state)
                        logger.debug(
                            "[pipeline-log] trace scheduling failed: %s", e,
                        )
        except RuntimeError:
            # 无 running loop（极少见）：退化为同步写，保证不丢日志
            if log_writer is not None:
                try:
                    log_writer.write_step(
                        request_id=request_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        step=step,
                        prompt=prompt,
                        response=response,
                        parsed=parsed,
                        memory_ids=mem_ids,
                        elapsed_ms=step_elapsed_ms,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        event_at_ms=event_at_ms,
                    )
                except Exception:
                    pass
            if trace_enabled:
                try:
                    result = original_store(
                        request_id=request_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        step=step,
                        prompt=prompt,
                        response=response,
                        parsed=parsed,
                        memory_ids=mem_ids,
                        elapsed_ms=step_elapsed_ms,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        event_at_ms=event_at_ms,
                        **extra_kwargs,
                    )
                    if asyncio.iscoroutine(result):
                        # 无法 await：丢弃，避免卡住
                        result.close()
                except Exception:
                    pass

        return True

    _store_pipeline_observability.trace_stats = _trace_stats
    return _store_pipeline_observability


async def _noop_store_pipeline_log(*args: Any, **kwargs: Any) -> bool:
    """Log 与 Trace 都关闭时的 store_pipeline_log：不计时、不写文件、不写库。"""
    return True


def install_pipeline_observability(
    cache: Any,
    *,
    log_label: str = "init",
) -> None:
    """给 cache 安装 JSONL + Trace 包装（fire-and-forget）。

    MEMORY_PIPELINE_LOG_ENABLED=false 时不写 JSONL；若 Trace 也关闭，
    store_pipeline_log 被换成空操作（不包装、不写库）。

    在 cache 所属 loop（write loop / _LoopThread）上调用，本函数会捕获该 loop
    作为 owner_loop 传给包装器：search 走独立 loop 后，从 search loop 触发的 DB
    落库会被派发回这个 owner_loop，避免跨 loop 用 aiomysql 污染连接池。
    """
    from .pipeline_log_writer import PipelineLogWriter

    try:
        owner_loop = asyncio.get_running_loop()
    except RuntimeError:
        owner_loop = None

    log_enabled = is_pipeline_log_enabled()
    trace_enabled = is_pipeline_trace_enabled()
    if not log_enabled and not trace_enabled:
        logger.info(
            f"[{log_label}] Pipeline log (JSONL) disabled ; pipeline trace (DB)=off ; "
            f"store_pipeline_log=no-op"
        )
        cache.store_pipeline_log = _noop_store_pipeline_log
        return

    log_writer = None
    if log_enabled:
        log_dir = resolve_pipeline_log_dir()
        log_writer = PipelineLogWriter(log_dir)
        logger.info(
            f"[{log_label}] Pipeline log (JSONL) enabled: {log_dir}/<subdir>/<date>.log "
            f"(subdir from user_id prefix before '__', else default) ; "
            f"pipeline trace (DB)={'on' if trace_enabled else 'off'} ; "
            f"persist=async(fire-and-forget)"
        )
    else:
        logger.info(
            f"[{log_label}] Pipeline log (JSONL) disabled ; "
            f"pipeline trace (DB)=on ; persist=async(fire-and-forget)"
        )
    cache.store_pipeline_log = wrap_store_pipeline_log(
        cache.store_pipeline_log,
        log_writer=log_writer,
        trace_enabled=trace_enabled,
        owner_loop=owner_loop,
    )
