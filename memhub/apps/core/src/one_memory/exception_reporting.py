"""One report path for exceptions that a catch would otherwise swallow.

Every site that catches `Exception` broadly must call `report_exception`
before (or instead of) continuing, so a failure never looks like success
and a stack is always on disk. Content (conversation text, keys, SQL
parameters, and exception messages) never reaches the diagnostic log: only
exception type, a repeat count, and file:line:function frames.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine
from time import monotonic
from typing import Any, cast

from one_memory.diagnostic_log import diagnostic_event, diagnostic_exception

_logger = logging.getLogger(__name__)
_REPORT_LOCK = threading.Lock()
_RECENT: Counter[tuple[str, str, str, str]] = Counter()
_LAST_FULL: dict[tuple[str, str, str, str], float] = {}
_FULL_INTERVAL_SECONDS = 60.0


def _safe_message(error: BaseException, limit: int = 300) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else f"{text[:limit]}…"


def _safe_frames(error: BaseException) -> tuple[str, ...]:
    return diagnostic_exception(error).get("safe_stack", ())  # type: ignore[return-value]


def report_exception(
    component: str,
    event: str,
    error: BaseException,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Write one error diagnostic (and mirror to core.log) for `error`.

    The same (component, event, error-type, file) key emits the full stack at
    most once per minute; repeats inside that window are counted.
    """
    key = (
        component,
        event,
        type(error).__name__,
        (_safe_frames(error)[-1] if _safe_frames(error) else "unknown"),
    )
    now = monotonic()
    with _REPORT_LOCK:
        _RECENT[key] += 1
        full = now - _LAST_FULL.get(key, 0.0) >= _FULL_INTERVAL_SECONDS
        if full:
            _LAST_FULL[key] = now
    payload = diagnostic_exception(error)
    diagnostic_event(
        level="error",
        component=component,
        event=event,
        outcome="failed",
        **payload,
        details={"count": _RECENT[key]},
    )
    if full:
        _logger.error(
            "%s: %s: %s",
            event,
            type(error).__name__,
            _safe_message(error),
            exc_info=error,
        )


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Report task exceptions nobody retrieves (the loop handler's job)."""

    previous = loop.get_exception_handler()

    def handler(loop_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        error = context.get("exception")
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
            return
        if isinstance(error, Exception):
            report_exception(
                "core.loop",
                "core.loop.task_exception",
                error,
                context={
                    "reason": str(context.get("message") or "unhandled task error")[:200],
                },
            )
        if previous is not None:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)


def install_threading_excepthook() -> None:
    """Report exceptions that kill a worker thread."""

    def hook(args: threading.ExceptHookArgs) -> None:
        error = args.exc_value
        if error is None or isinstance(error, (SystemExit, KeyboardInterrupt)):
            return
        if isinstance(error, Exception):
            report_exception(
                "core.thread",
                "core.thread.exception",
                error,
                context={"reason": f"thread {args.thread and args.thread.name}"},
            )

    threading.excepthook = hook


def watched_task[T](
    loop: asyncio.AbstractEventLoop,
    factory: Callable[[], asyncio.Future[T] | Awaitable[T]],
    *,
    component: str = "core.task",
    **kwargs: Any,
) -> asyncio.Future[T] | None:
    """Create a task and attach a done-callback that reports late failures."""

    def _done(task: asyncio.Future[T]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if isinstance(error, Exception):
            report_exception(component, "core.task.exception", error)

    try:
        awaitable = factory()
        if isinstance(awaitable, asyncio.Future):
            task = awaitable
        elif inspect.iscoroutine(awaitable):
            task = loop.create_task(cast(Coroutine[Any, Any, T], awaitable), **kwargs)
        else:
            task = asyncio.ensure_future(awaitable, loop=loop)
    except Exception as error:
        report_exception(component, "core.task.exception", error)
        return None
    task.add_done_callback(_done)
    return task
