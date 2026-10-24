"""Real ASGI validation must precede creation reservation and secret writes."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.secrets import InMemorySecretStore, SecretStoreUnavailableError

TABLE = "model_profile_creation_operations"
KEY = "synthetic-validation-boundary-key"
TOKEN = "synthetic-validation-boundary-token"


def stored_counts(database: Path) -> tuple[int, int, int | None]:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        profiles = db.execute("SELECT COUNT(*) FROM model_profiles").fetchone()[0]
        versions = db.execute("SELECT COUNT(*) FROM model_profile_versions").fetchone()[0]
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
        operations = db.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] if exists else None
        return profiles, versions, operations


class CountingSecrets(InMemorySecretStore):
    def __init__(self, database: Path) -> None:
        super().__init__()
        self.database = database
        self.put_calls = 0
        self.delete_calls = 0
        self.reservations_at_put: list[tuple[str, str, int]] = []
        self.unavailable = False

    def put(self, secret_ref: str, value: str) -> None:
        self.put_calls += 1
        # A real DB read at the secret boundary proves the positive reservation order.
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                f"SELECT operation_id,state,created_revision FROM {TABLE} WHERE profile_id=?",
                (secret_ref.split(":")[1],),
            ).fetchone()
        assert row is not None and row[1:] == ("pending", 1)
        self.reservations_at_put.append(row)
        if self.unavailable:
            raise SecretStoreUnavailableError("synthetic secure storage unavailable")
        super().put(secret_ref, value)

    def delete(self, secret_ref: str) -> None:
        self.delete_calls += 1
        super().delete(secret_ref)


def body(operation_id: str) -> dict[str, object]:
    return {
        "name": "Validation boundary fixture",
        "provider_kind": "openai_compatible",
        "base_url": "https://generation.example.invalid/v1",
        "model": "fixture-model",
        "embedding_provider_kind": "local_onnx",
        "embedding_model": "bge-small-zh-v1.5",
        "embedding_dimensions": 512,
        "api_key": KEY,
        "creation_operation_id": operation_id,
    }


@asynccontextmanager
async def application(tmp_path: Path):
    data = (tmp_path / "data").resolve()
    secrets = CountingSecrets(data / "one-memory.sqlite3")
    settings = Settings(
        _env_file=None,
        data_dir=data,
        source_home=tmp_path / "empty-source-home",
        api_token=TOKEN,
        imports_enabled=False,
        live_sync_enabled=False,
        live_sync_watch_enabled=False,
        automatic_memory_processing=False,
    )
    app = create_app(settings, source_catalog={}, secret_store=secrets)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client,
    ):
        assert stored_counts(secrets.database) == (0, 0, None)
        assert secrets.put_calls == secrets.delete_calls == 0
        yield app, client, secrets
        assert app.state.memory_task_store.list() == []
        assert app.state.model_profile_store.active_identity() is None


async def receipt(client: AsyncClient, operation_id: str) -> dict[str, object]:
    response = await client.get(f"/api/v1/model-profile-creations/{operation_id}")
    assert response.status_code == 200
    assert KEY not in response.text
    return response.json()


@pytest.mark.parametrize("kind", ["public-http", "cross-origin-embedding"])
@pytest.mark.parametrize("reuse_operation_id", [True, False], ids=["same-id", "new-id"])
def test_validation_rejection_precedes_reservation_and_can_be_corrected(
    tmp_path: Path, kind: str, reuse_operation_id: bool
) -> None:
    async def scenario() -> None:
        async with application(tmp_path) as (_, client, secrets):
            operation_id = str(uuid4())
            valid = body(operation_id)
            if kind == "public-http":
                invalid = {**valid, "base_url": "http://generation.example.invalid/v1"}
            else:
                valid.update(
                    embedding_provider_kind="openai_compatible",
                    embedding_model="fixture-embedding",
                    embedding_base_url="https://generation.example.invalid/v1",
                    embedding_dimensions=None,
                )
                invalid = {**valid, "embedding_base_url": "https://other.example.invalid/v1"}
            before = stored_counts(secrets.database)
            response = await client.post("/api/v1/model-profiles", json=invalid)
            assert response.status_code == 422
            assert response.json()["detail"] == "request_validation_failed"
            assert response.json()["errors"]
            assert KEY not in response.text
            assert "input" not in response.text
            assert secrets.put_calls == secrets.delete_calls == 0
            assert secrets.reservations_at_put == []
            assert stored_counts(secrets.database) == before == (0, 0, None)
            assert await receipt(client, operation_id) == {
                "operation_id": operation_id,
                "state": "not_found",
                "profile_id": None,
                "created_revision": None,
            }
            assert (await client.get("/api/v1/model-profiles")).json() == []
            assert stored_counts(secrets.database) == (0, 0, None)

            # Only correcting the rejected input now reaches reserve -> secret -> commit.
            next_id = operation_id if reuse_operation_id else str(uuid4())
            valid["creation_operation_id"] = next_id
            created = await client.post("/api/v1/model-profiles", json=valid)
            assert created.status_code == 200, created.text
            profile = created.json()
            assert profile["revision"] == 1 and not profile["is_active"]
            assert KEY not in created.text
            assert await receipt(client, next_id) == {
                "operation_id": next_id,
                "state": "committed",
                "profile_id": profile["profile_id"],
                "created_revision": 1,
            }
            assert secrets.put_calls == 1 and secrets.delete_calls == 0
            assert secrets.reservations_at_put == [(next_id, "pending", 1)]
            assert stored_counts(secrets.database) == (1, 1, 1)
            assert len((await client.get("/api/v1/model-profiles")).json()) == 1
            if not reuse_operation_id:
                assert (await receipt(client, operation_id))["state"] == "not_found"

    asyncio.run(scenario())


def test_creation_conflict_is_409_not_validation_rejection(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with application(tmp_path) as (_, client, secrets):
            operation_id = str(uuid4())
            request = body(operation_id)
            created = await client.post("/api/v1/model-profiles", json=request)
            assert created.status_code == 200
            original = await receipt(client, operation_id)
            assert original["state"] == "committed"
            assert secrets.put_calls == 1
            replay = await client.post("/api/v1/model-profiles", json=request)
            assert replay.status_code == 409
            assert replay.json() == {"detail": "model_creation_operation_conflict"}
            assert KEY not in replay.text
            assert await receipt(client, operation_id) == original
            assert secrets.put_calls == 1 and secrets.delete_calls == 0
            assert stored_counts(secrets.database) == (1, 1, 1)

    asyncio.run(scenario())


def test_secure_storage_503_retains_reservation_and_cannot_be_replayed(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with application(tmp_path) as (_, client, secrets):
            operation_id = str(uuid4())
            request = body(operation_id)
            secrets.unavailable = True
            response = await client.post("/api/v1/model-profiles", json=request)
            assert response.status_code == 503
            assert response.json() == {"detail": "secure_storage_unavailable"}
            assert KEY not in response.text
            assert secrets.put_calls == 1 and secrets.delete_calls == 0
            assert secrets.reservations_at_put == [(operation_id, "pending", 1)]
            pending = await receipt(client, operation_id)
            assert pending["state"] == "pending"
            assert pending["profile_id"] and pending["created_revision"] == 1
            assert stored_counts(secrets.database) == (0, 0, 1)
            assert (await client.get("/api/v1/model-profiles")).json() == []
            secrets.unavailable = False
            replay = await client.post("/api/v1/model-profiles", json=request)
            assert replay.status_code == 409
            assert replay.json() == {"detail": "model_creation_operation_conflict"}
            assert secrets.put_calls == 1
            assert await receipt(client, operation_id) == pending
            assert stored_counts(secrets.database) == (0, 0, 1)

    asyncio.run(scenario())
