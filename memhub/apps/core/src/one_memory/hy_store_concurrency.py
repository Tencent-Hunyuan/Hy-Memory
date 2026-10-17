"""Process-local coordination for HY's native stores."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_WRITE_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def native_store_write_lock() -> asyncio.Lock:
    """Return the write lock for the current event loop."""
    loop = asyncio.get_running_loop()
    lock = _WRITE_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _WRITE_LOCKS[loop] = lock
    return lock


@asynccontextmanager
async def native_store_read_slot() -> AsyncIterator[bool]:
    """Admit a native HY read only while no write is active.

    Chroma's native reader may retain the GIL after an asyncio timeout. Reads
    therefore share the physical-store lock, but never wait behind a writer:
    host recall can immediately fall back to the governance ledger.
    """
    lock = native_store_write_lock()
    if lock.locked():
        yield False
        return
    await lock.acquire()
    try:
        yield True
    finally:
        lock.release()
