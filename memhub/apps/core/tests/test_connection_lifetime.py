"""Every SQLite connection a request opens must be closed when the request ends.

Since Python 3.11 a connection's statement cache refers back to the connection,
so one that is never closed is freed only by the cyclic collector, not when it
goes out of scope. In the long-running core, with a heap large enough that full
collections are rare, the interface's polling alone reached the per-process
limit of 61,440 open files; every later open then failed and the app answered
`local_store_unavailable` for everything.
"""

import asyncio
import gc
import sqlite3
from contextlib import contextmanager

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.sources.registry import build_source_catalog


def _open_connections() -> int:
    return sum(isinstance(item, sqlite3.Connection) for item in gc.get_objects())


@contextmanager
def _without_cyclic_collection():
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
        gc.collect()


def test_polled_endpoints_close_every_connection_they_open(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    home.mkdir()
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
            live_sync_watch_enabled=False,
        ),
        source_catalog=build_source_catalog(home=home, environment={}),
    )

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            paths = sorted(
                route.path
                for route in app.routes
                if "GET" in getattr(route, "methods", ()) and "{" not in route.path
            )
            assert "/api/v1/sources" in paths
            for path in paths:
                await client.get(path)
            with _without_cyclic_collection():
                before = _open_connections()
                for path in paths:
                    await client.get(path)
                left_open = _open_connections() - before
            assert left_open == 0, f"{left_open} connections left for the collector"

    asyncio.run(scenario())
