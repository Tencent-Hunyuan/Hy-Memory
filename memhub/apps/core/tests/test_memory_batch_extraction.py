import asyncio
import json

import httpx
import pytest

from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_batch_extraction import (
    MemoryBatchExtractionError,
    MemoryBatchExtractor,
)
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind


def snapshot() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="profile",
        revision=1,
        provider_kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        model="memory-model",
        embedding_model="embedding-model",
        embedding_base_url="http://127.0.0.1:11434",
        embedding_dimensions=768,
        timeout_seconds=30,
        max_concurrency=1,
        context_window=8_192,
        has_api_key=False,
    )


def records(tmp_path):
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    return tuple(
        store.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"message-{index}",
                correlation_id=f"message-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=verbatim,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        for index, verbatim in enumerate(
            (
                "讨论界面时，我觉得简洁、克制的中文文案更适合我。",
                "请实现这个页面并运行测试。",
            )
        )
    )


def test_batch_extractor_keeps_only_unique_known_evidence_ids(tmp_path) -> None:
    source_records = records(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "memory-model"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        evidence_id = source_records[0].evidence_id
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "memories": [
                                        {
                                            "evidence_id": source_records[1].evidence_id,
                                            "support_quote": "请实现这个页面",
                                            "content": "用户" + ("过长" * 100),
                                            "layer": "L2_FACT",
                                        },
                                        {
                                            "evidence_id": evidence_id,
                                            "support_quote": "我觉得简洁、克制的中文文案更适合我",
                                            "content": "用户偏好简洁、克制的中文界面文案。",
                                            "layer": "L2_FACT",
                                        },
                                        {
                                            "evidence_id": evidence_id,
                                            "support_quote": "我觉得简洁、克制的中文文案更适合我",
                                            "content": "用户偏好重复结果。",
                                            "layer": "L2_FACT",
                                        },
                                        {
                                            "evidence_id": source_records[1].evidence_id,
                                            "support_quote": "原文里不存在的引文",
                                            "content": "用户偏好不存在的引文。",
                                            "layer": "L2_FACT",
                                        },
                                        {
                                            "evidence_id": "invented",
                                            "support_quote": "不存在",
                                            "content": "用户偏好不存在的结果。",
                                            "layer": "L2_FACT",
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    extractor = MemoryBatchExtractor(transport=httpx.MockTransport(handler))

    result = asyncio.run(extractor.extract(source_records, profile=snapshot(), api_key=None))

    assert len(result) == 1
    assert result[0].evidence_id == source_records[0].evidence_id
    assert result[0].content == "用户偏好简洁、克制的中文界面文案。"


def test_batch_extractor_maps_provider_failure_to_machine_error(tmp_path) -> None:
    extractor = MemoryBatchExtractor(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503))
    )

    with pytest.raises(MemoryBatchExtractionError) as captured:
        asyncio.run(extractor.extract(records(tmp_path), profile=snapshot(), api_key=None))

    assert str(captured.value) == "memory_batch_extraction_failed"
