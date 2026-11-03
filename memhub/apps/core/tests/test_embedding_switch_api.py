"""The embedding switch through Core's HTTP surface and lifespan.

Activation refusals and the empty-home path, a whole switch driven over the
API with writes paused and intake still queueing, the startup merge of a
stray space before the drain starts, and `embedding_unavailable` on recall
and context search when the remote embedding does not answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import embedding_health
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.model_profiles import ModelCapabilities, ModelProfileInput, ProviderKind
from one_memory.secrets import InMemorySecretStore
from tests.embedding_fakes import FakeReembedBackend

pytestmark = pytest.mark.fresh_sdk

ENDPOINT = "https://models.example/v1"


def builtin_base() -> str:
    identity = "\0".join(("local_onnx", "local", "bge-small-zh-v1.5", "512"))
    return "memhub_" + hashlib.sha256(identity.encode()).hexdigest()[:16]


@pytest.fixture
def backend(monkeypatch) -> FakeReembedBackend:
    fake = FakeReembedBackend()
    monkeypatch.setattr(
        "one_memory.embedding_switch.default_reembed_backend", lambda _data_dir: fake
    )
    return fake


def application(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("switch-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer switch-test"},
    )


async def recovered(client) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = (await client.get("/api/v1/health")).json()
        if body["recovery"] in {"done", "failed"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("recovery did not finish")


async def until(client, predicate, *, seconds: float = 10.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = (await client.get("/api/v1/embedding-switches/current")).json()
        if predicate(body):
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"switch never reached the expected state: {body}")


def remote_profile(app, *, model="bge-m3", dims=1024, name="custom", endpoint=ENDPOINT):
    store, secrets = app.state.model_profile_store, app.state.secret_store
    created = store.create(
        ModelProfileInput(
            name=name,
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url=endpoint,
            model="chat",
            embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            embedding_base_url=endpoint,
            embedding_model=model,
            embedding_dimensions=dims,
            api_key=SecretStr("sk-test"),
        ),
        secrets,
    )
    store.record_capabilities(
        created.profile_id,
        expected_revision=created.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    return created


def test_activating_another_embedding_answers_with_the_plan(tmp_path, backend):
    backend.store.put(builtin_base(), 512, {"a": ("one", "local"), "b": ("two", "bob")})

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            profile = remote_profile(app)

            response = await client.post(f"/api/v1/model-profiles/{profile.profile_id}/activate")

            assert response.status_code == 409
            body = response.json()
            assert body["detail"] == "embedding_switch_required"
            plan = body["plan"]
            assert plan["needed"] is True and plan["total_vectors"] == 2 and plan["users"] == 2
            assert plan["source"]["model"] == "bge-small-zh-v1.5"
            assert (plan["target"]["model"], plan["target"]["dims"]) == ("bge-m3", 1024)
            assert app.state.model_profile_store.active_identity() is None

    asyncio.run(scenario())


def test_an_empty_home_activates_another_embedding_directly(tmp_path, backend):
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            builtin = app.state.embedding_spaces.require_live()
            profile = remote_profile(app)

            response = await client.post(f"/api/v1/model-profiles/{profile.profile_id}/activate")

            assert response.status_code == 200, response.text
            assert response.json()["is_active"] is True
            live = app.state.embedding_spaces.require_live()
            assert (live.model, live.dims) == ("bge-m3", 1024)
            assert app.state.embedding_spaces.get(builtin.space_id).state == "dropped"
            # The same embedding again: a plain activation, no plan.
            again = remote_profile(app, name="same model, other chat model")
            assert (
                await client.post(f"/api/v1/model-profiles/{again.profile_id}/activate")
            ).status_code == 200
            assert app.state.embedding_spaces.require_live().space_id == live.space_id

    asyncio.run(scenario())


def test_a_switch_over_the_api_pauses_writes_and_keeps_intake(tmp_path, backend):
    backend.store.put(
        builtin_base(),
        512,
        {f"m{i}": (f"memory {i}", "local") for i in range(6)},
        model="bge-small-zh-v1.5",
    )

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            old = app.state.embedding_spaces.require_live()
            profile = remote_profile(app)
            planned = await client.post(
                "/api/v1/embedding-switches/plan", json={"profile_id": profile.profile_id}
            )
            assert planned.status_code == 200
            plan = planned.json()
            stale = await client.post(
                "/api/v1/embedding-switches",
                json={
                    "profile_id": profile.profile_id,
                    "expected_revision": profile.revision,
                    "plan_token": "stale",
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"] == "embedding_switch_plan_stale"

            backend.pause = asyncio.Event()
            started = await client.post(
                "/api/v1/embedding-switches",
                json={
                    "profile_id": profile.profile_id,
                    "expected_revision": profile.revision,
                    "plan_token": plan["plan_token"],
                    "requested_by": "cli",
                },
            )
            assert started.status_code == 202, started.text
            job_id = started.json()["job_id"]
            running = await until(
                client, lambda body: (body["job"] or {}).get("state") == "reembedding"
            )
            assert running["writes_paused"] is True and running["phase"] == "running"

            # Direct writers wait; intake keeps queueing; status shows the switch.
            edit = await client.put(
                f"/api/v2/memories/{uuid4()}", json={"content": "changed", "user_id": "local"}
            )
            assert edit.status_code == 409
            assert edit.json()["detail"] == "embedding_switch_in_progress"
            delete = await client.delete(f"/api/v2/memories/{uuid4()}")
            assert delete.status_code == 409
            added = await client.post(
                "/api/v2/memories",
                json={"request_id": str(uuid4()), "text": "请记住这件事", "scope": "all-apps"},
            )
            assert added.status_code == 201
            processing = (await client.get("/api/v1/memory-processing")).json()
            assert processing["embedding_switch"]["job_id"] == job_id
            assert (await client.get("/api/v1/health")).json()["embedding_switch"]["kind"] == (
                "switch"
            )
            other = remote_profile(app, model="text-embedding-v3", dims=768, name="other")
            refused = await client.post(f"/api/v1/model-profiles/{other.profile_id}/activate")
            assert refused.json()["detail"] == "embedding_switch_running"

            backend.pause.set()
            done = await until(
                client, lambda body: (body["last_finished"] or {}).get("job_id") == job_id
            )
            assert done["last_finished"]["state"] == "done"
            assert done["writes_paused"] is False
            assert app.state.model_profile_store.active_identity()[0] == profile.profile_id

            spaces = (await client.get("/api/v1/embedding-spaces")).json()
            states = {space["space_id"]: space["state"] for space in spaces["spaces"]}
            assert states[old.space_id] == "retired"
            assert spaces["live_space_id"] == done["last_finished"]["target"]["space_id"]
            cleaned = await client.delete(f"/api/v1/embedding-spaces/{old.space_id}")
            assert cleaned.status_code == 200
            assert cleaned.json()["space"]["state"] == "dropped"
            live_cleanup = await client.delete(
                f"/api/v1/embedding-spaces/{spaces['live_space_id']}"
            )
            assert live_cleanup.json()["detail"] == "embedding_space_not_retired"

            # Same model and width behind another endpoint: no re-embed.
            proxied = remote_profile(app, name="proxy", endpoint="https://proxy.example/v1")
            moved = await client.post(f"/api/v1/model-profiles/{proxied.profile_id}/activate")
            assert moved.status_code == 200, moved.text
            assert app.state.embedding_spaces.require_live().space_id == spaces["live_space_id"]

    asyncio.run(scenario())


def test_graph_backfill_blocks_starting_an_embedding_switch(tmp_path, backend):
    backend.store.put(
        builtin_base(),
        512,
        {"m1": ("memory", "local")},
        model="bge-small-zh-v1.5",
    )

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            profile = remote_profile(app)
            plan = (
                await client.post(
                    "/api/v1/embedding-switches/plan",
                    json={"profile_id": profile.profile_id},
                )
            ).json()
            hold = asyncio.Event()
            task = asyncio.create_task(hold.wait())
            app.state.memory_graph_backfill._task = task
            try:
                response = await client.post(
                    "/api/v1/embedding-switches",
                    json={
                        "profile_id": profile.profile_id,
                        "expected_revision": profile.revision,
                        "plan_token": plan["plan_token"],
                    },
                )
                assert response.status_code == 409
                assert response.json()["detail"] == "memory_lifecycle_busy"
                assert app.state.embedding_switch.writes_open() is True
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app.state.memory_graph_backfill._task = None

    asyncio.run(scenario())


def test_cancel_over_the_api(tmp_path, backend):
    backend.store.put(builtin_base(), 512, {f"m{i}": (f"m {i}", "local") for i in range(4)})

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            profile = remote_profile(app)
            plan = (
                await client.post(
                    "/api/v1/embedding-switches/plan", json={"profile_id": profile.profile_id}
                )
            ).json()
            backend.pause = asyncio.Event()
            job_id = (
                await client.post(
                    "/api/v1/embedding-switches",
                    json={
                        "profile_id": profile.profile_id,
                        "expected_revision": profile.revision,
                        "plan_token": plan["plan_token"],
                    },
                )
            ).json()["job_id"]
            await until(client, lambda body: (body["job"] or {}).get("state") == "reembedding")

            cancelled = await client.post(f"/api/v1/embedding-switches/{job_id}/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["cancel_requested"]
            backend.pause.set()
            done = await until(client, lambda body: body["last_finished"] is not None)

            assert done["last_finished"]["state"] == "rolled_back"
            assert done["last_finished"]["error_code"] == "embedding_switch_cancelled"
            missing = await client.post(f"/api/v1/embedding-switches/{uuid4()}/cancel")
            assert missing.status_code == 404

    asyncio.run(scenario())


def test_startup_merges_a_stray_space_before_writes_resume(tmp_path, backend):
    backend.store.put(builtin_base(), 512, {"live-1": ("live", "local")})
    stray = "memhub_" + "a" * 16
    backend.store.put(stray, 1024, {"stray-1": ("left by an older build", "hermes-1")})

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            # Core answers at once; the merge runs behind the loading screen.
            assert (await client.get("/api/v1/health")).status_code == 200
            await recovered(client)
            done = await until(client, lambda body: body["last_finished"] is not None)

            assert done["last_finished"]["kind"] == "merge"
            assert done["last_finished"]["state"] == "done"
            assert backend.store.ids(builtin_base(), 512) == {"live-1", "stray-1"}
            spaces = (await client.get("/api/v1/embedding-spaces")).json()
            stray_row = next(row for row in spaces["spaces"] if row["collection_base"] == stray)
            assert stray_row["state"] == "retired" and stray_row["keep_until"]
            assert app.state.embedding_switch.writes_open()

    asyncio.run(scenario())


class _EmptySearchClient:
    def search(self, *_args, **_kwargs):
        return {"request_id": "r", "memories": {"schema": [], "proactive": [], "normal": []}}

    def get(self, *_args, **_kwargs):
        return None


def test_a_dead_remote_embedding_answers_embedding_unavailable(tmp_path, backend, monkeypatch):
    status = {"code": 503}

    def transport():
        return httpx.MockTransport(
            lambda request: httpx.Response(
                status["code"], json={"data": [{"embedding": [0.1] * 4}]}
            )
        )

    monkeypatch.setattr(embedding_health, "transport_factory", transport)
    monkeypatch.setattr(
        "one_memory.hy_sdk_runtime.acquire_client",
        lambda *args, **kwargs: SimpleNamespace(client=_EmptySearchClient(), close=lambda: None),
    )

    async def ready(_app):
        return True

    monkeypatch.setattr("one_memory.api._recall_model_ready", ready)

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await recovered(client)
            profile = remote_profile(app)
            assert (
                await client.post(f"/api/v1/model-profiles/{profile.profile_id}/activate")
            ).status_code == 200

            recall = await client.post("/api/v1/recall", json={"query": "数据库"})
            assert recall.status_code == 503
            assert recall.json()["detail"] == "embedding_unavailable"
            context = await client.post(
                "/api/v2/context/search",
                json={"query": "数据库", "user_id": "local", "kinds": ["memory"]},
            )
            assert context.status_code == 503
            assert context.json()["detail"] == "embedding_unavailable"

            # The service answers again: an empty result is just empty.
            status["code"] = 200
            app.state.sdk_client_handle.health.forget()
            recall = await client.post("/api/v1/recall", json={"query": "数据库"})
            assert recall.status_code == 200

    asyncio.run(scenario())
