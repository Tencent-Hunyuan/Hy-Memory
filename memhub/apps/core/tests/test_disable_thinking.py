"""运行期生成请求须按档案可选关闭思考；默认不向厂商下发未知字段。"""

from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import SecretStr

from one_memory.hy_sdk_runtime import build_sdk_config
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_batch_extraction import MemoryBatchExtractor
from one_memory.memory_qa import _answer_request
from one_memory.model_probe import _structured_request
from one_memory.model_profiles import (
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore


def _capture(handler):
    payloads: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode()))
        return handler(request)

    return payloads, respond


def _evidence(database):
    return (
        EvidenceStore(database)
        .put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="c",
                message_id="m",
                correlation_id="m",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim="我一直偏好简洁回答。",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
        .record
    )


def _ok_batch(evidence_id: str) -> httpx.Response:
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
                                        "evidence_id": evidence_id,
                                        "support_quote": "我一直偏好简洁回答",
                                        "content": "用户偏好简洁回答",
                                        "layer": "L2_FACT",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        },
    )


def test_batch_extraction_omits_thinking_fields_when_thinking_enabled(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = store.create(
        ModelProfileInput(
            name="openai",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            disable_thinking=False,
        ),
        secrets,
    )
    evidence = _evidence(database)
    payloads, respond = _capture(lambda _req: _ok_batch(evidence.evidence_id))
    snapshot = store.snapshot(profile.profile_id, profile.revision)
    asyncio.run(
        MemoryBatchExtractor(transport=httpx.MockTransport(respond)).extract(
            [evidence],
            profile=snapshot,
            api_key="sk-test",
        )
    )
    assert "reasoning_effort" not in payloads[0]
    assert "think" not in payloads[0]


def test_batch_extraction_disables_thinking_by_default(tmp_path) -> None:
    """A profile created without an explicit choice opts out of the chain.

    Regression guard. The flag used to default to False, which meant every
    first run paid the measured 12x extraction latency and hit the 60s
    timeout that surfaced as `hy_memory_model_request_failed`. The default
    here is the product decision, not an implementation detail, so it is
    asserted rather than assumed.
    """
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = store.create(
        ModelProfileInput(
            name="openai",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
        ),
        secrets,
    )
    assert profile.disable_thinking is True
    evidence = _evidence(database)
    payloads, respond = _capture(lambda _req: _ok_batch(evidence.evidence_id))
    snapshot = store.snapshot(profile.profile_id, profile.revision)
    asyncio.run(
        MemoryBatchExtractor(transport=httpx.MockTransport(respond)).extract(
            [evidence],
            profile=snapshot,
            api_key="sk-test",
        )
    )
    assert payloads[0]["reasoning_effort"] == "none"
    assert "think" not in payloads[0]


def test_batch_extraction_sends_reasoning_effort_none_when_disabled(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = store.create(
        ModelProfileInput(
            name="openai",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            disable_thinking=True,
        ),
        secrets,
    )
    evidence = _evidence(database)
    payloads, respond = _capture(lambda _req: _ok_batch(evidence.evidence_id))
    snapshot = store.snapshot(profile.profile_id, profile.revision)
    asyncio.run(
        MemoryBatchExtractor(transport=httpx.MockTransport(respond)).extract(
            [evidence],
            profile=snapshot,
            api_key="sk-test",
        )
    )
    assert payloads[0]["reasoning_effort"] == "none"
    assert "think" not in payloads[0]


def test_openai_ask_payload_omits_thinking_unless_profile_opts_in(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    off = store.create(
        ModelProfileInput(
            name="off",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            disable_thinking=False,
        ),
        secrets,
    )
    on = store.create(
        ModelProfileInput(
            name="on",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            disable_thinking=True,
        ),
        secrets,
    )
    _m, _u, _h, payload_off = _answer_request(off, "sk-test", "我偏好什么？", ())
    assert "reasoning_effort" not in payload_off
    _m, _u, _h, payload_on = _answer_request(on, "sk-test", "我偏好什么？", ())
    assert payload_on["reasoning_effort"] == "none"


def test_ollama_batch_sends_think_false_only_when_disabled(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = store.create(
        ModelProfileInput(
            name="ollama",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="qwen",
            disable_thinking=True,
        ),
        secrets,
    )
    evidence = _evidence(database)
    payloads, respond = _capture(
        lambda _req: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"memories": []}, ensure_ascii=False)}}
                ]
            },
        )
    )
    snapshot = store.snapshot(profile.profile_id, profile.revision)
    asyncio.run(
        MemoryBatchExtractor(transport=httpx.MockTransport(respond)).extract(
            [evidence],
            profile=snapshot,
            api_key=None,
        )
    )
    assert payloads[0]["think"] is False
    assert "reasoning_effort" not in payloads[0]


def _deepseek(store, secrets, *, disable_thinking: bool):
    return store.create(
        ModelProfileInput(
            name=f"deepseek-{disable_thinking}",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-flash",
            api_key=SecretStr("sk-test"),
            embedding_model="embedding-model",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
            disable_thinking=disable_thinking,
        ),
        secrets,
    )


def test_sdk_config_turns_deepseek_thinking_off_like_the_probe(tmp_path) -> None:
    """DeepSeek Flash spent the SDK's 2000 agent tokens thinking: EMPTY_RESPONSE.

    The SDK sends `llm.extra_body` on every chat call (chat and the shared
    coding/working provider), and the probe must ask the model the same way.
    """
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = _deepseek(store, secrets, disable_thinking=True)
    snapshot = store.snapshot(profile.profile_id, profile.revision)

    config = build_sdk_config(snapshot, api_key="sk-test", data_dir=tmp_path / "data")

    assert config.llm.extra_body == {"thinking": {"type": "disabled"}}
    _m, _u, _h, probe = _structured_request(profile, "sk-test")
    assert probe["thinking"] == config.llm.extra_body["thinking"]
    assert "reasoning_effort" not in probe


def test_sdk_config_sends_no_thinking_fields_unless_the_profile_opts_in(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = _deepseek(store, secrets, disable_thinking=False)
    snapshot = store.snapshot(profile.profile_id, profile.revision)

    config = build_sdk_config(snapshot, api_key="sk-test", data_dir=tmp_path / "data")

    assert not config.llm.extra_body


def test_sdk_config_maps_other_providers_through_the_same_table(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    openai = store.create(
        ModelProfileInput(
            name="openai",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            embedding_model="embedding-model",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        ),
        secrets,
    )
    ollama = store.create(
        ModelProfileInput(
            name="ollama",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="qwen",
            embedding_model="embedding-model",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
        ),
        secrets,
    )

    openai_config = build_sdk_config(
        store.snapshot(openai.profile_id, openai.revision),
        api_key="sk-test",
        data_dir=tmp_path / "data",
    )
    ollama_config = build_sdk_config(
        store.snapshot(ollama.profile_id, ollama.revision),
        api_key=None,
        data_dir=tmp_path / "data",
    )

    assert openai_config.llm.extra_body == {"reasoning_effort": "none"}
    assert ollama_config.llm.extra_body == {"think": False}
