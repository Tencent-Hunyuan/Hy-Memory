import asyncio
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.database_access import main_database
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.migration_runner import SqliteMigrationStore
from one_memory.memory_server.migrations import Migration
from one_memory.secrets import InMemorySecretStore


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        source_home=tmp_path / "source",
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
        api_token=SecretStr("test-token"),
    )


def _database(path, *, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(path)
    store.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            message_id=value,
            correlation_id=value,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=value,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    asyncio.run(SqliteMigrationStore(path).record(Migration(34, "synthetic", lambda _: None)))


# A serving Core refuses a legacy home outright, so this one starts on SDK.
@pytest.mark.fresh_sdk
def test_restore_endpoint_refuses_when_runtime_owns_the_core(tmp_path) -> None:
    monkeypatch_app = create_app(_settings(tmp_path), manage_runtime=True)

    async def scenario() -> None:
        async with monkeypatch_app.router.lifespan_context(monkeypatch_app):
            transport = ASGITransport(app=monkeypatch_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/backup/restore",
                    headers={"Authorization": "Bearer test-token"},
                    json={"source": "backups/x.sqlite3"},
                )
                assert response.status_code == 409
                assert response.json()["detail"] == "backup_restore_runtime_running"

    asyncio.run(scenario())


def test_restore_endpoint_restores_a_managed_backup(tmp_path) -> None:
    data = tmp_path / "data"
    _database(data / "one-memory.sqlite3", value="old")
    app = create_app(_settings(data), secret_store=InMemorySecretStore())

    async def prepare() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(prepare())
    source = data / "backups" / "good.sqlite3"
    main_database(data / "one-memory.sqlite3").back_up(source)
    _database(data / "one-memory.sqlite3", value="after-backup")

    async def scenario() -> None:
        restored = create_app(_settings(data), secret_store=InMemorySecretStore())
        async with restored.router.lifespan_context(restored):
            transport = ASGITransport(app=restored)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/backup/restore",
                    headers={"Authorization": "Bearer test-token"},
                    json={"source": "backups/good.sqlite3"},
                )
                assert response.status_code == 200
                assert response.json()["state"] == "restored"

    asyncio.run(scenario())

    with sqlite3.connect(data / "one-memory.sqlite3") as connection:
        assert connection.execute("SELECT verbatim FROM evidence ORDER BY verbatim").fetchall() == [
            ("old",)
        ]


def test_restore_endpoint_rejects_a_source_outside_home(tmp_path) -> None:
    data = tmp_path / "data"
    _database(data / "one-memory.sqlite3", value="old")
    outside = tmp_path / "outside.sqlite3"
    _database(outside, value="outside")
    app = create_app(_settings(data), secret_store=InMemorySecretStore())

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/backup/restore",
                    headers={"Authorization": "Bearer test-token"},
                    json={"source": str(outside)},
                )
                assert response.status_code == 422
                assert response.json()["detail"] == "backup_restore_source_outside_home"

    asyncio.run(scenario())
