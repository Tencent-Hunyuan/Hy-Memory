"""HY writes must publish the native-store write lock readers check."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from one_memory.hy_memory_bridge import HyMemoryBridge
from one_memory.hy_store_concurrency import native_store_read_slot


class _BlockingClient:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def update(self, memory_id, content):
        self.started.set()
        self.release.wait(timeout=5)
        return {"success": True}


def test_bridge_write_blocks_native_read_slot(tmp_path):
    async def _run():
        client = _BlockingClient()
        bridge = HyMemoryBridge(
            profile=SimpleNamespace(),
            api_key=None,
            data_dir=Path(tmp_path),
        )

        async def _get_client():
            return client

        bridge._get_client = _get_client

        task = asyncio.create_task(bridge.update("memory-1", "new content"))
        assert await asyncio.to_thread(client.started.wait, 2)

        async with native_store_read_slot() as admitted:
            assert admitted is False

        client.release.set()
        await task

        async with native_store_read_slot() as admitted:
            assert admitted is True

    asyncio.run(_run())
