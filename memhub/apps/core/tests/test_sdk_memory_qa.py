from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from one_memory.hy_memory_bridge import HySdkMemoryRecord
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


class Bridge:
    def __init__(self) -> None:
        self.current = record()
        self.searches = 0

    async def search_memories(self, *_args, **_kwargs):
        self.searches += 1
        return (SimpleNamespace(memory_id="memory-1"),)

    async def get_sdk_memory(self, memory_id):
        assert memory_id == "memory-1"
        return self.current


class Profiles:
    @staticmethod
    def active(_secrets):
        return None


def test_sdk_ask_uses_native_sdk_memory_without_governance_fields() -> None:
    bridge = Bridge()
    answer = asyncio.run(
        SdkMemoryAnswerer(
            bridge=bridge,  # type: ignore[arg-type]
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
    bridge = Bridge()

    class ChangingProfiles:
        @staticmethod
        def active(_secrets):
            bridge.current = record("内容已变化")
            return None

    with pytest.raises(MemoryAnswerError, match="memory_answer_context_changed"):
        asyncio.run(
            SdkMemoryAnswerer(
                bridge=bridge,  # type: ignore[arg-type]
                profiles=ChangingProfiles(),  # type: ignore[arg-type]
                secrets=object(),  # type: ignore[arg-type]
            ).answer("偏好是什么？")
        )


def test_sdk_ask_propagates_sdk_search_failure_without_legacy_fallback() -> None:
    class Failed(Bridge):
        async def search_memories(self, *_args, **_kwargs):
            raise RuntimeError("sdk unavailable")

    with pytest.raises(RuntimeError, match="sdk unavailable"):
        asyncio.run(
            SdkMemoryAnswerer(
                bridge=Failed(),  # type: ignore[arg-type]
                profiles=Profiles(),  # type: ignore[arg-type]
                secrets=object(),  # type: ignore[arg-type]
            ).answer("偏好是什么？")
        )
