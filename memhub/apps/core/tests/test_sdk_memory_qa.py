from __future__ import annotations

import asyncio

import pytest

from one_memory.hy_sdk_types import HySdkMemoryRecord
from one_memory.memory_qa import MemoryAnswerError
from one_memory.sdk_memory_qa import SdkMemoryAnswerer


def record(content: str = "用户偏好本地优先。") -> HySdkMemoryRecord:
    return HySdkMemoryRecord(
        memory_id="memory-1",
        content=content,
        layer="l2_fact",
        status="active",
        is_latest=True,
        user_id="local",
        agent_id="one-memory",
        source="cursor",
        gmt_created=1_790_000_000,
    )


class Client:
    def __init__(self) -> None:
        self.current = record()
        self.searches = 0

    def search(self, *_args, **_kwargs):
        self.searches += 1
        return {
            "scene": "normal",
            "memories": [{"memory_id": "memory-1", "score": 0.9}],
        }

    def get(self, memory_id):
        assert memory_id == "memory-1"
        return self.current.model_dump()


class Handle:
    def __init__(self, client: Client) -> None:
        self.value = client

    async def client(self):
        return self.value


class Profiles:
    @staticmethod
    def active(_secrets):
        return None


def test_sdk_ask_uses_native_sdk_memory_without_governance_fields() -> None:
    client = Client()
    answer = asyncio.run(
        SdkMemoryAnswerer(
            client_handle=Handle(client),
            profiles=Profiles(),  # type: ignore[arg-type]
            secrets=object(),  # type: ignore[arg-type]
        ).answer("偏好是什么？")
    )

    assert answer.schema_version == 2
    assert answer.mode == "extractive"
    assert answer.memories[0].memory_id == "memory-1"
    assert answer.memories[0].content == "用户偏好本地优先。"
    body = answer.model_dump()
    assert "revision" not in str(body)
    assert "evidence_ids" not in str(body)
    assert "scopes" not in str(body)


def test_sdk_ask_revalidates_memory_before_returning_extract() -> None:
    client = Client()

    class ChangingProfiles:
        @staticmethod
        def active(_secrets):
            client.current = record("内容已变化")
            return None

    with pytest.raises(MemoryAnswerError, match="memory_answer_context_changed"):
        asyncio.run(
            SdkMemoryAnswerer(
                client_handle=Handle(client),
                profiles=ChangingProfiles(),  # type: ignore[arg-type]
                secrets=object(),  # type: ignore[arg-type]
            ).answer("偏好是什么？")
        )


def test_sdk_ask_propagates_sdk_search_failure_without_legacy_fallback() -> None:
    class Failed(Client):
        def search(self, *_args, **_kwargs):
            raise RuntimeError("sdk unavailable")

    with pytest.raises(RuntimeError, match="sdk unavailable"):
        asyncio.run(
            SdkMemoryAnswerer(
                client_handle=Handle(Failed()),
                profiles=Profiles(),  # type: ignore[arg-type]
                secrets=object(),  # type: ignore[arg-type]
            ).answer("偏好是什么？")
        )
