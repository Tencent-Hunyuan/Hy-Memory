import asyncio
import json

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.sdk_memory_qa import SdkAskMemory


def _sse_events(text: str) -> list[dict]:
    return [
        json.loads(frame.strip().removeprefix("data:").strip())
        for frame in text.split("\n\n")
        if frame.strip().startswith("data:")
    ]


def test_ask_stream_endpoint_requires_the_local_token(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            )
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Origin": "http://127.0.0.1:5173"},
            ) as client,
        ):
            response = await client.post(
                "/api/v2/memories/ask/stream",
                json={"question": "我是谁？", "scope": "all-apps"},
            )

            assert response.status_code == 401

    asyncio.run(scenario())


def test_sdk_ask_v2_stream_is_native(tmp_path, monkeypatch) -> None:
    memory = SdkAskMemory(
        memory_id="memory-1",
        content="用户要求真实数据。",
        layer="l2_fact",
        status="active",
        source="cursor",
        gmt_created=1_790_000_000,
    )

    class Answerer:
        async def answer_stream(self, _question):
            yield {"type": "recall", "memories": (memory,)}
            yield {"type": "delta", "text": "你要求真实数据。"}
            yield {
                "type": "final",
                "schema_version": 2,
                "answer": "你要求真实数据。",
                "memories": (memory,),
                "mode": "extractive",
            }

    monkeypatch.setattr(api, "_sdk_request_answerer", lambda _app: Answerer())

    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            )
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
                "/api/v2/memories/ask/stream",
                json={"question": "要求是什么？"},
            )
            assert response.status_code == 200
            events = _sse_events(response.text)
            assert events[0]["memories"] == [
                {
                    "memory_id": "memory-1",
                    "content": "用户要求真实数据。",
                    "layer": "l2_fact",
                    "status": "active",
                    "source": "cursor",
                    "gmt_created": 1_790_000_000,
                }
            ]
            assert events[-1]["schema_version"] == 2
            assert "revision" not in response.text
            assert "evidence_ids" not in response.text

    asyncio.run(scenario())
