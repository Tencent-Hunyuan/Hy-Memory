"""The guarded to_thread adapter preserves asyncio.to_thread context semantics."""
import asyncio
import contextvars
import importlib.util
from pathlib import Path
import unittest


# This helper is dependency-free; exercise it without initializing the SDK.
_spec = importlib.util.spec_from_file_location(
    "async_executor_under_test",
    Path(__file__).resolve().parents[1] / "hy_memory/utils/async_executor.py",
)
_executor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_executor)


class ThreadContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_copies_context_and_does_not_propagate_worker_changes(self):
        request_id = contextvars.ContextVar("request_id", default="missing")
        request_id.set("caller")

        def work(prefix, *, suffix):
            value = request_id.get()
            request_id.set("worker")
            return prefix + value + suffix

        result = await _executor.to_thread_guarded(work, "[", suffix="]")
        self.assertEqual(result, "[caller]")
        self.assertEqual(request_id.get(), "caller")

    async def test_concurrent_callers_keep_separate_contexts(self):
        request_id = contextvars.ContextVar("request_id", default="missing")

        async def call(value):
            request_id.set(value)
            await asyncio.sleep(0)
            return await _executor.to_thread_guarded(request_id.get)

        self.assertEqual(await asyncio.gather(call("a"), call("b")), ["a", "b"])

    async def test_context_is_copied_when_coroutine_runs(self):
        request_id = contextvars.ContextVar("request_id", default="missing")
        request_id.set("created")
        pending = _executor.to_thread_guarded(request_id.get)
        request_id.set("awaited")
        self.assertEqual(await pending, "awaited")


if __name__ == "__main__":
    unittest.main()
