"""Failure-only cleanup for resources created during client/runtime startup."""

import asyncio
from concurrent.futures import Future, TimeoutError
import logging
import threading

logger = logging.getLogger(__name__)
_LOOP_CLEANUP_TIMEOUT = 5.0


async def close_component(name, close):
    """Keep closing dependencies without replacing the initialization exception."""
    try:
        await close()
    except BaseException:
        logger.warning("[init rollback] %s close failed", name, exc_info=True)


async def rollback_metrics(state, cache):
    """Undo this initialization's singleton binding and newly started tasks."""
    if state is None:
        return
    metrics, previous_cache, already_started = state
    if not already_started:
        await close_component("metrics", metrics.stop_background_tasks)
    if metrics._cache is cache:
        metrics.bind_cache(previous_cache)


async def finish_cleanup(awaitable):
    """Join cleanup even if the caller receives another cancellation request."""
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    return task.result()


async def initialize_component(component):
    """Drain startup before rollback; executor work cannot be forcibly cancelled."""
    task = asyncio.create_task(component.initialize())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # A cache/vector initializer may still be allocating native resources in
        # a shielded executor future. Closing it before that finishes can leak a
        # connection allocated *after* close() inspected its empty fields.
        try:
            await finish_cleanup(task)
        except BaseException:
            logger.warning("[init rollback] initializer failed during cancellation", exc_info=True)
        raise


async def run_initialization(loop_thread, initialize, rollback):
    """Propagate caller cancellation to startup, then join its rollback on-owner."""
    owner_task = None

    async def on_owner():
        nonlocal owner_task
        owner_task = asyncio.current_task()
        await initialize()

    waiter = asyncio.create_task(loop_thread.run_async(on_owner()))
    try:
        return await asyncio.shield(waiter)
    except asyncio.CancelledError:
        async def cancel_on_owner():
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()
                await asyncio.gather(owner_task, return_exceptions=True)
            # Cancellation can also arrive after startup completed on-owner but
            # before its result was delivered. Startup has not committed yet.
            await rollback()

        # run_async shields its underlying concurrent future. Cancel the actual
        # owner-loop task, and keep the waiter alive until its finally finishes.
        await finish_cleanup(loop_thread.run_async(cancel_on_owner()))
        await finish_cleanup(asyncio.gather(waiter, return_exceptions=True))
        raise


def stop_failed_loops(*loop_threads):
    """Best-effort owned-loop cleanup; never replace the startup exception."""
    for loop_thread in loop_threads:
        if loop_thread is None:
            continue
        try:
            if not loop_thread._close_on_exit:
                loop_thread._close_on_exit = True
                loop_thread._run_finished.add_done_callback(
                    lambda _, owner=loop_thread: close_failed_loop(owner)
                )
            _stop_failed_loop(loop_thread)
        except BaseException:
            logger.warning("[init rollback] loop cleanup failed", exc_info=True)


def _stop_failed_loop(loop_thread):
    loop, thread = loop_thread._loop, loop_thread._thread
    if loop.is_closed():
        if thread.is_alive():
            logger.warning("[init rollback] loop already closed but its thread is still alive")
        return

    if thread.is_alive():
        if loop.is_running() and loop_thread._ready.is_set():
            completed = Future()

            async def shutdown():
                await close_component("async generators", loop.shutdown_asyncgens)
                await close_component("default executor", loop.shutdown_default_executor)

            def shutdown_done(task):
                try:
                    task.result()
                except BaseException:
                    logger.warning("[init rollback] loop shutdown failed", exc_info=True)
                finally:
                    loop.stop()
                    completed.set_result(None)

            def begin_shutdown():
                # Construct the coroutine on-owner. If the loop stops between
                # the state check and submission, no unawaited coroutine leaks.
                task = loop.create_task(shutdown())
                task.add_done_callback(shutdown_done)

            loop.call_soon_threadsafe(begin_shutdown)
            if thread is threading.current_thread():
                logger.warning("[init rollback] owner-thread cleanup deferred; cannot join itself")
                return
            try:
                completed.result(timeout=_LOOP_CLEANUP_TIMEOUT)
            except TimeoutError:
                # Leave shutdown queued/running so a temporarily busy owner can
                # finish it later. Cancelling or closing here races its resources.
                logger.warning("[init rollback] loop shutdown timed out; cleanup remains deferred")
        else:
            logger.warning("[init rollback] loop not ready or no longer running; requesting stop")
            # A startup still blocked before run_forever can consume this stop
            # if it later resumes. Do not submit or wait for a coroutine here.
            loop.call_soon_threadsafe(loop.stop)
            if thread is threading.current_thread():
                logger.warning("[init rollback] owner-thread cleanup deferred; cannot join itself")
                return
        thread.join(timeout=_LOOP_CLEANUP_TIMEOUT)

    if thread.is_alive():
        logger.warning(
            "[init rollback] loop thread did not stop; cannot safely force termination; "
            "leaving loop open until owner exits; failed-owner finalization remains armed"
        )
    else:
        close_failed_loop(loop_thread)


def close_failed_loop(loop_thread):
    """Close after owner _run returns, or after join confirms owner exit."""
    try:
        loop = loop_thread._loop
        if loop.is_closed():
            return
        if loop.is_running():
            logger.warning("[init rollback] loop still running; refusing to close it")
            return
        loop.close()
    except BaseException:
        logger.warning("[init rollback] loop final close failed", exc_info=True)
