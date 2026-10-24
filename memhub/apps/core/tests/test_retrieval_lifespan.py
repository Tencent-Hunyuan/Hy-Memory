import asyncio
import sqlite3
from contextlib import closing

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.secrets import InMemorySecretStore


def test_startup_does_not_create_a_private_retrieval_index(tmp_path, monkeypatch):
    """Raw reads go through the SDK and lexical FTS; MemHub keeps no vector copy."""
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )
        app = create_app(settings, secret_store=InMemorySecretStore())
        async with app.router.lifespan_context(app):
            assert not hasattr(app.state, "retrieval_runtime")
            assert not (tmp_path / "retrieval-vectors").exists()
            with closing(sqlite3.connect(tmp_path / "one-memory.sqlite3")) as db:
                assert (
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name LIKE 'retrieval_%'"
                    ).fetchall()
                    == []
                )

    asyncio.run(scenario())
