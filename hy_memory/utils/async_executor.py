"""Reliable awaiting for synchronous work submitted to thread executors."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
from concurrent.futures import Executor
from typing import Any, Callable, Optional


_COMPLETION_GUARD_SECONDS = 0.05


async def run_in_executor_guarded(
    executor: Optional[Executor],
    func: Callable[..., Any],
    *args: Any,
    guard_seconds: float = _COMPLETION_GUARD_SECONDS,
    **kwargs: Any,
) -> Any:
    """Await one executor task while guarding against a lost loop wakeup.

    The underlying task is submitted exactly once and shielded from the guard
    timeout.  The timeout only wakes an idle selector so a queued completion
    callback can be processed.
    """
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    future = loop.run_in_executor(executor, call)
    interval = max(0.01, float(guard_seconds))
    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=interval)
        except asyncio.TimeoutError:
            if future.done():
                return future.result()


async def to_thread_guarded(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Guarded equivalent of ``asyncio.to_thread`` using the default pool."""
    context = contextvars.copy_context()
    call = functools.partial(func, *args, **kwargs)
    return await run_in_executor_guarded(None, context.run, call)


def concurrent_result_guarded(
    future: "concurrent.futures.Future[Any]",
    target_loop: asyncio.AbstractEventLoop,
    *,
    guard_seconds: float = _COMPLETION_GUARD_SECONDS,
) -> Any:
    """Synchronously wait for a cross-loop Future with periodic target wakeups."""
    interval = max(0.01, float(guard_seconds))
    while True:
        try:
            return future.result(timeout=interval)
        except concurrent.futures.TimeoutError:
            if future.done():
                return future.result()
            target_loop.call_soon_threadsafe(lambda: None)


async def await_concurrent_future_guarded(
    future: "concurrent.futures.Future[Any]",
    target_loop: asyncio.AbstractEventLoop,
    *,
    guard_seconds: float = _COMPLETION_GUARD_SECONDS,
) -> Any:
    """Asynchronously wait for a cross-loop Future with periodic wakeups."""
    wrapped = asyncio.wrap_future(future)
    interval = max(0.01, float(guard_seconds))
    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=interval)
        except asyncio.TimeoutError:
            if wrapped.done():
                return wrapped.result()
            target_loop.call_soon_threadsafe(lambda: None)
