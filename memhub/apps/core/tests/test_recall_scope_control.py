import asyncio
import json
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import opaque_workspace_id
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.memory_governance import MemoryDraft, MemoryKind
from one_memory.secrets import InMemorySecretStore


def test_external_scope_cannot_override_shared_or_host_workspace_boundary(tmp_path):
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
            source = app.state.evidence_store.put(
                EvidenceInput(
                    source_id="manual",
                    conversation_id="scope",
                    message_id="1",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="Project meeting avoids Friday",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            scopes = ["private", "all-apps", opaque_workspace_id("claude-code", "/test/project")]
            ids = []
            for scope in scopes:
                item = app.state.person_writes.create(
                    MemoryDraft(
                        user_id="local",
                        kind=MemoryKind.CHAT,
                        content="Project meeting avoids Friday",
                        scopes=(scope,),
                        evidence_ids=(source.evidence_id,),
                    ),
                    request_id=f"create-{scope}",
                )
                ids.append(item.memory_id)
            semantic = AsyncMock(return_value=())
            app.state.recall_target.semantic_search = semantic
            app.state.memory_answerer.semantic_search = semantic
            external = {
                "X-One-Memory-Read-Context": "external",
                "X-One-Memory-Installation-Id": "24361b44-5a15-4f87-ad09-05571dc281c3",
                "X-One-Memory-Installation-Revision": "1",
                "X-One-Memory-Allowed-Tools": json.dumps(["om_recall", "om_ask"]),
                "X-One-Memory-Allowed-Scopes": json.dumps(["all-apps", scopes[2]]),
            }
            for endpoint, key in [
                ("/api/v1/recall", "query"),
                ("/api/v1/memories/ask", "question"),
                ("/api/v1/memories/ask/stream", "question"),
            ]:
                for scope in ["*", "private", scopes[2], "unrelated-scope"]:
                    response = await client.post(
                        endpoint, json={key: "Project meeting", "scope": scope}, headers=external
                    )
                    assert response.status_code == 403
                semantic.assert_not_awaited()
            normal = await client.post(
                "/api/v1/recall", json={"query": "Project meeting"}, headers=external
            )
            assert [hit["memory_id"] for hit in normal.json()["hits"]] == [ids[1]]
            workspace = await client.post(
                "/api/v1/recall",
                json={
                    "query": "Project meeting",
                    "scope": scopes[2],
                    "workspace_path": "/test/project",
                },
                headers=external,
            )
            assert [hit["memory_id"] for hit in workspace.json()["hits"]] == [ids[2]]
            denied_expansion = await client.post(
                "/api/v1/recall",
                json={"query": "Project meeting", "workspace_path": "/test/project"},
                headers={**external, "X-One-Memory-Allowed-Scopes": '["all-apps"]'},
            )
            assert denied_expansion.status_code == 200
            assert [hit["memory_id"] for hit in denied_expansion.json()["hits"]] == [ids[1]]
            internal = await client.post(
                "/api/v1/recall", json={"query": "Project meeting", "scope": "*"}
            )
            assert {hit["memory_id"] for hit in internal.json()["hits"]} == set(ids)

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
