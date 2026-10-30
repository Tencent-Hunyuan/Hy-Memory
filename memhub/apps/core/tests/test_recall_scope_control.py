import asyncio
import json
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import opaque_workspace_id
from one_memory.secrets import InMemorySecretStore


def test_external_scope_cannot_override_shared_or_host_workspace_boundary(tmp_path, monkeypatch):
    # Memory recall, not the raw fallback a home without a model would take.
    async def model_ready(_app):
        return True

    monkeypatch.setattr("one_memory.api._recall_model_ready", model_ready)

    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path,
                api_token=SecretStr("scope-test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer scope-test"},
            ) as client,
        ):
            workspace = opaque_workspace_id("claude-code", "/test/project")
            semantic = AsyncMock(return_value=())
            app.state.recall_target.semantic_search = semantic
            external = {
                "X-One-Memory-Read-Context": "external",
                "X-One-Memory-Installation-Id": "24361b44-5a15-4f87-ad09-05571dc281c3",
                "X-One-Memory-Installation-Revision": "1",
                "X-One-Memory-Allowed-Tools": json.dumps(["om_recall", "om_ask"]),
                "X-One-Memory-Allowed-Scopes": json.dumps(["all-apps", workspace]),
            }
            for endpoint, key in [
                ("/api/v1/recall", "query"),
                ("/api/v2/memories/ask", "question"),
                ("/api/v2/memories/ask/stream", "question"),
            ]:
                for scope in ["*", "private", workspace, "unrelated-scope"]:
                    response = await client.post(
                        endpoint, json={key: "Project meeting", "scope": scope}, headers=external
                    )
                    assert response.status_code == 403
                semantic.assert_not_awaited()
            normal = await client.post(
                "/api/v1/recall", json={"query": "Project meeting"}, headers=external
            )
            assert normal.status_code == 200
            semantic.assert_awaited()

    asyncio.run(scenario())


def test_per_memory_recall_scope_mutation_is_not_a_product_api(tmp_path):
    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path,
                api_token=SecretStr("scope-test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer scope-test"},
            ) as client,
        ):
            response = await client.post(
                "/api/v1/memories/memory-1/scopes",
                json={
                    "request_id": "scope-change",
                    "expected_revision": 1,
                    "scopes": ["all-apps"],
                },
            )
            assert response.status_code == 404

    asyncio.run(scenario())
