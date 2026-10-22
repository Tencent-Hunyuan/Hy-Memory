"""What the pinned public SDK's MetricsCollector does, and what that costs us.

These integration tests ask whether the pending-task warning is a leak that
grows per request, and whether reusing one client is enough to avoid it.
Reading the warning alone cannot answer either question, so the tests measure it.

The answer that came out is narrower and worse than "a leak": the collector is
a process singleton, so the two loops start once rather than once per client --
but the flag that says "already started" is sticky and there is no stop. Once
the loop those tasks were created on is gone, a later loop gets nothing, and
flush and cleanup are silently dead for the rest of the process.

That makes reusing the client necessary but not sufficient: what has to survive
is the event loop. These tests pin both halves so an SDK upgrade that fixes it
shows up as a failure here instead of going unnoticed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("hy_memory")


def background_loop_count() -> int:
    return sum(
        1
        for task in asyncio.all_tasks()
        if task.get_coro() is not None
        and (task.get_coro().__qualname__ or "").startswith("MetricsCollector._")
    )


def test_the_collector_is_one_per_process_not_one_per_client() -> None:
    """If it were per-client, reusing the client would be the whole fix."""
    from hy_memory.metrics import MetricsCollector

    assert MetricsCollector.get() is MetricsCollector.get()


def test_metrics_collector_can_stop_and_restart_on_a_second_event_loop(
    monkeypatch,
) -> None:
    """The current SDK owns an explicit lifecycle and restarts cleanly."""
    from hy_memory.metrics import MetricsCollector

    # A fresh collector, so an earlier test in the session cannot decide this.
    monkeypatch.setattr(MetricsCollector, "_instance", None)
    collector = MetricsCollector.get()

    async def cycle() -> tuple[int, int]:
        await collector.start_background_tasks()
        started = background_loop_count()
        await collector.stop_background_tasks()
        return started, background_loop_count()

    first = asyncio.run(cycle())
    second = asyncio.run(cycle())

    assert first == (2, 0)
    assert second == (2, 0)


def test_the_runtime_reuses_one_client_per_profile_revision(tmp_path: Path) -> None:
    """Our side of the workaround: never build a client per request.

    A new client per request would re-enter HY initialization every time; the
    collector would survive as a singleton but everything else would not.
    """
    from one_memory.memory_processing import MemoryProcessingSupervisor

    built = []

    def factory(profile, secret, data_dir):
        built.append((profile.profile_id, profile.revision))
        return object()

    resolver = MemoryProcessingSupervisor._bridge
    queue = object.__new__(MemoryProcessingSupervisor)
    queue._bridges = {}
    queue.bridge_factory = factory

    profile = _snapshot()
    first = resolver(queue, profile, None, tmp_path)
    again = resolver(queue, profile, None, tmp_path)
    newer = resolver(queue, profile.model_copy(update={"revision": 2}), None, tmp_path)

    assert first is again, "a second request built a second client"
    assert newer is not first, "a new revision reused the old revision's client"
    assert built == [("profile", 1), ("profile", 2)]


def _snapshot():
    from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

    return ModelProfileSnapshot(
        profile_id="profile",
        revision=1,
        provider_kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        model="memory-model",
        embedding_model="builtin",
        embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        embedding_base_url=None,
        embedding_dimensions=384,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8192,
        has_api_key=False,
    )
