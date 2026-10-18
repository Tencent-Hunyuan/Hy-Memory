import asyncio

import httpx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.model_probe import ModelProber
from one_memory.model_profiles import ModelCapabilities
from one_memory.secrets import InMemorySecretStore

API_KEY = "api-route-secret"


def test_model_profile_api_never_returns_key_and_enforces_revision(tmp_path) -> None:
    async def scenario() -> None:
        secrets = InMemorySecretStore()
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=secrets,
        )
        headers = {
            "Authorization": "Bearer local-token",
            "Origin": "http://127.0.0.1:5173",
        }
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            created = await client.post(
                "/api/v1/model-profiles",
                json={
                    "name": "Provider",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://models.example.com/v1",
                    "model": "memory-model",
                    "embedding_model": "embedding-model",
                    "api_key": API_KEY,
                },
            )
            assert created.status_code == 200
            assert created.json()["has_api_key"] is True
            assert API_KEY not in created.text
            profile_id = created.json()["profile_id"]
            app.state.model_profile_store.record_capabilities(
                profile_id,
                expected_revision=created.json()["revision"],
                capabilities=ModelCapabilities(
                    endpoint_reachable=True,
                    model_available=True,
                    structured_output=True,
                    embedding_available=True,
                ),
                secrets=secrets,
            )

            stale = await client.patch(
                f"/api/v1/model-profiles/{profile_id}",
                json={"expected_revision": 999, "model": "stale"},
            )
            assert stale.status_code == 409

            activated = await client.post(f"/api/v1/model-profiles/{profile_id}/activate")
            assert activated.status_code == 200
            assert activated.json()["is_active"] is True

            deleted = await client.delete(f"/api/v1/model-profiles/{profile_id}")
            assert deleted.status_code == 204
            assert secrets.resolve(f"model-profile:{profile_id}:revision:1") is None

            repeated = await client.delete(f"/api/v1/model-profiles/{profile_id}")
            assert repeated.status_code == 404

    asyncio.run(scenario())


def test_model_profile_probe_route_returns_only_machine_results(tmp_path) -> None:
    async def scenario() -> None:
        secrets = InMemorySecretStore()
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=secrets,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
            if request.url.path.endswith("/embeddings"):
                return httpx.Response(
                    200,
                    json={"data": [{"embedding": [0.1, 0.2]}]},
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]},
            )

        headers = {
            "Authorization": "Bearer local-token",
            "Origin": "http://127.0.0.1:5173",
        }
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            created = await client.post(
                "/api/v1/model-profiles",
                json={
                    "name": "Provider",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://models.example.com/v1",
                    "model": "memory-model",
                    "embedding_model": "embedding-model",
                    "api_key": API_KEY,
                },
            )
            profile_id = created.json()["profile_id"]
            app.state.model_prober = ModelProber(
                store=app.state.model_profile_store,
                secrets=secrets,
                transport=httpx.MockTransport(handler),
            )

            probed = await client.post(f"/api/v1/model-profiles/{profile_id}/probe")

            assert probed.status_code == 200
            assert probed.json()["capabilities"]["structured_output"] is True
            assert API_KEY not in probed.text

    asyncio.run(scenario())


def test_model_profile_validation_never_echoes_api_key(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={
                    "Authorization": "Bearer local-token",
                    "Origin": "http://127.0.0.1:5173",
                },
            ) as client,
        ):
            response = await client.post(
                "/api/v1/model-profiles",
                json={
                    "name": "Provider",
                    "provider_kind": "openai_compatible",
                    "base_url": "http://models.example.com/v1",
                    "model": "memory-model",
                    "api_key": API_KEY,
                },
            )

            assert response.status_code == 422
            assert response.json()["detail"] == "request_validation_failed"
            assert API_KEY not in response.text
            assert "input" not in response.text

    asyncio.run(scenario())


def test_activation_survives_a_corpus_already_imported(tmp_path) -> None:
    """Activating with evidence present used to fail outright.

    Scheduling the catch-up needs the event loop, and a synchronous path
    operation is handed a worker thread that has none — so the moment there
    was anything to catch up on, activation raised instead of activating.
    Importing before configuring a model is the ordinary order, so this was
    reachable by doing the expected thing.
    """

    async def scenario() -> None:
        secrets = InMemorySecretStore()
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=secrets,
        )
        headers = {
            "Authorization": "Bearer local-token",
            "Origin": "http://127.0.0.1:5173",
        }
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            app.state.evidence_store.put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="conversation:already-here",
                    message_id="message:already-here",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="统一用 axios 封装，不用 fetch 裸调",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
            created = await client.post(
                "/api/v1/model-profiles",
                json={
                    "name": "Provider",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://models.example.com/v1",
                    "model": "memory-model",
                    "embedding_model": "embedding-model",
                    "api_key": API_KEY,
                },
            )
            profile_id = created.json()["profile_id"]
            app.state.model_profile_store.record_capabilities(
                profile_id,
                expected_revision=created.json()["revision"],
                capabilities=ModelCapabilities(
                    endpoint_reachable=True,
                    model_available=True,
                    structured_output=True,
                    embedding_available=True,
                ),
                secrets=secrets,
            )
            readmissions: list[bool] = []
            app.state.memory_write_queue.readmit_model_profile_waiters = (
                lambda: readmissions.append(True) or 0
            )

            activated = await client.post(f"/api/v1/model-profiles/{profile_id}/activate")

            assert activated.status_code == 200
            assert activated.json()["is_active"] is True
            assert API_KEY not in activated.text
            assert readmissions == [True]
            # Started, not waited for: the walk reads every conversation.
            assert not app.state.memory_backfill.done()

    asyncio.run(scenario())
