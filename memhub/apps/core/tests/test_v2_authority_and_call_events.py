"""v2 is the only memory data plane, and host calls through it are history."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.sdk_data_plane_migration import MemoryAuthority
from one_memory.sdk_memory_qa import SdkAskMemory, SdkMemoryAnswer
from one_memory.secrets import InMemorySecretStore

EXTERNAL = {
    "x-one-memory-read-context": "external",
    "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
    "x-one-memory-installation-revision": "1",
    "x-one-memory-allowed-scopes": json.dumps(["all-apps", "source:cursor"]),
    "x-one-memory-allowed-tools": json.dumps(["om_recall", "om_ask", "om_conversations"]),
    "x-one-memory-host-id": "cursor",
}

MEMORY = SdkAskMemory(
    memory_id="memory-1",
    content="用户要求真实数据。",
    layer="l2_fact",
    status="active",
    source="cursor",
)


def application(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("v2-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
            call_event_retention_days=30,
            call_event_max_events=1000,
        ),
        secret_store=InMemorySecretStore(),
    )


def client_for(app, headers=None):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer v2-test", **(headers or {})},
    )


class Answerer:
    async def answer(self, question):
        return SdkMemoryAnswer(answer="真实数据。", memories=(MEMORY,), mode="extractive")

    async def answer_stream(self, question):
        yield {"type": "recall", "memories": (MEMORY,)}
        yield {
            "type": "final",
            "schema_version": 2,
            "answer": "真实数据。",
            "memories": (MEMORY,),
            "mode": "extractive",
        }


class Sdk:
    def search(self, query, **kwargs):
        if kwargs.get("layers") == ["l1_raw"]:
            return {"memories": {"schema": [], "proactive": [], "normal": []}}
        return {
            "memories": {
                "schema": [],
                "proactive": [],
                "normal": [{"memory_id": "memory-1", "content": "real data", "score": 0.9}],
            }
        }

    def get(self, memory_id):
        return {
            "memory_id": memory_id,
            "content": "real data",
            "layer": "l2_fact",
            "status": "active",
            "user_id": "local",
            "agent_id": "all-apps",
            "is_latest": True,
        }


class Handle:
    async def client(self):
        return Sdk()

    async def close(self):
        return None


RETIRED_V1_DATA_PLANE = (
    ("GET", "/api/v2/memory-authority", None),
    ("GET", "/api/v1/home-summary", None),
    ("GET", "/api/v1/memory-library", None),
    ("GET", "/api/v1/memories", None),
    ("GET", "/api/v1/memories/graph", None),
    ("GET", "/api/v1/memories/memory-1", None),
    ("POST", "/api/v1/memories/ask", {"question": "release?"}),
    ("GET", "/api/v1/conversations/search?query=release", None),
    ("GET", "/api/v1/conversation-browser", None),
    ("POST", "/api/v1/context/search", {"query": "release"}),
    ("POST", "/api/v1/boundary/peek", {}),
    ("GET", "/api/v1/memory-tasks", None),
    ("POST", "/api/v1/memory-writes", {}),
)


@pytest.mark.fresh_sdk
def test_sdk_authority_keeps_v2_open(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_sdk_request_answerer", lambda _app: Answerer())
    app = application(tmp_path)

    async def scenario():
        async with app.router.lifespan_context(app), client_for(app) as client:
            assert app.state.memory_authority is MemoryAuthority.SDK
            app.state.sdk_client_handle = Handle()
            ask = await client.post("/api/v2/memories/ask", json={"question": "release?"})
            assert ask.status_code == 200, ask.text
            context = await client.post(
                "/api/v2/context/search", json={"query": "release", "kinds": ["memory"]}
            )
            assert context.status_code == 200, context.text
            for method, path, body in RETIRED_V1_DATA_PLANE:
                response = await client.request(method, path, json=body)
                assert response.status_code in {404, 405}, (method, path, response.text)

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_v2_host_calls_are_recorded_and_renderer_calls_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_sdk_request_answerer", lambda _app: Answerer())
    app = application(tmp_path)

    async def scenario():
        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle()
            async with client_for(app) as renderer:
                await renderer.post("/api/v2/memories/ask", json={"question": "release?"})
                await renderer.post("/api/v2/memories/ask/stream", json={"question": "release?"})
                await renderer.post(
                    "/api/v2/context/search", json={"query": "release", "kinds": ["memory"]}
                )
            assert app.state.call_event_store.list().total == 0

            async with client_for(app, EXTERNAL) as host:
                ask = await host.post("/api/v2/memories/ask", json={"question": "release?"})
                assert ask.status_code == 200, ask.text
                assert ask.headers["X-One-Memory-Call-Recorded"] == "true"
                stream = await host.post(
                    "/api/v2/memories/ask/stream", json={"question": "release?"}
                )
                assert stream.status_code == 200, stream.text
                context = await host.post(
                    "/api/v2/context/search", json={"query": "release", "kinds": ["memory"]}
                )
                assert context.status_code == 200, context.text
                assert context.headers["X-One-Memory-Call-Recorded"] == "true"
                probe = await host.post("/api/v2/memories/ask", json={"question": "__om_probe__"})
                assert probe.status_code == 200

            page = app.state.call_event_store.list()
            assert page.total == 3
            events = sorted(page.items, key=lambda event: event.tool)
            assert [event.tool for event in events] == ["om_ask", "om_ask", "om_recall"]
            for event in events:
                assert event.host_id == "cursor"
                assert event.state == "grounded"
                # SDK items attest no revision; history keeps counts only.
                assert event.references == ()
            assert [event.memory_count for event in events] == [1, 1, 1]
            assert str(events[2].event_id) == context.json()["request_id"]

            history = await client_for(app).__aenter__()
            try:
                listed = await history.get("/api/v1/call-events")
            finally:
                await history.__aexit__(None, None, None)
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == 3

    asyncio.run(scenario())
