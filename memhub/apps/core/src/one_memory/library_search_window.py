"""Short-lived result windows for the memory library's keyword search.

A library search asks the SDK once for a bounded relevance window and keeps
the keyword matches. Paging must cut every page from that same list: running
the search again per page can rank differently and duplicate or skip memories
between pages. The matches are kept here under an opaque ``window_id`` that
the client passes back with the next offset.

A window is bound to the filters it was searched with (query, source,
statuses, scene). It expires after ``ttl_seconds`` without being read, and the
store keeps at most ``max_windows`` windows, dropping the least recently read
first.
Only memory identities are kept, never content: each page is read fresh from
the SDK, so an edit or a delete shows on the next page read.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from one_memory.hy_sdk_types import LibraryHit

DEFAULT_WINDOW_TTL_SECONDS = 300.0
DEFAULT_MAX_WINDOWS = 32

WindowKey = tuple[str, str | None, tuple[str, ...], str]


def window_key(
    query: str, source: str | None, statuses: list[str], scene: str = "all"
) -> WindowKey:
    return (query, source, tuple(sorted(set(statuses))), scene)


@dataclass(frozen=True)
class SearchWindow:
    window_id: str
    key: WindowKey
    hits: tuple[LibraryHit, ...]
    # Set when the SDK window was full, so matches outside it were not looked
    # for and `total` may undercount.
    search_window: int | None


@dataclass
class _Entry:
    window: SearchWindow
    last_read: float = field(default=0.0)


class LibrarySearchWindows:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_WINDOW_TTL_SECONDS,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max(1, max_windows)
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, window_id: str, key: WindowKey) -> SearchWindow | None:
        """The window, if it is still live and was searched with `key`."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            entry = self._entries.get(window_id)
            if entry is None or entry.window.key != key:
                return None
            entry.last_read = now
            self._entries.move_to_end(window_id)
            return entry.window

    def put(
        self,
        key: WindowKey,
        hits: tuple[LibraryHit, ...],
        search_window: int | None,
    ) -> SearchWindow:
        window = SearchWindow(
            window_id=secrets.token_urlsafe(12),
            key=key,
            hits=hits,
            search_window=search_window,
        )
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._entries[window.window_id] = _Entry(window=window, last_read=now)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return window

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _expire(self, now: float) -> None:
        stale = [
            window_id
            for window_id, entry in self._entries.items()
            if now - entry.last_read >= self._ttl
        ]
        for window_id in stale:
            del self._entries[window_id]
