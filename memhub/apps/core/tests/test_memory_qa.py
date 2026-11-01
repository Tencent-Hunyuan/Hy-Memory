import asyncio
import json

import httpx
import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_qa import MemoryAnswerer, MemoryAnswerError
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from tests.person_door import as_person


@pytest.mark.parametrize(
    "stop_term",
    tuple("我你他她它的了是在和与或这那也都很没有就还而但却因且把被从给到又著着过啊呢吗吧"),
)
def test_search_terms_keep_meaningful_single_chinese_character(stop_term) -> None:
    from one_memory.memory_qa import _search_terms

    assert _search_terms("张") == {"张"}
    assert _search_terms(stop_term) == set()
    assert _search_terms(f"{stop_term} 张") == {"张"}


def _grounded_setup(tmp_path, *, memory_id: str = "memory-real-data"):
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    profiles = ModelProfileStore(database)
    secrets = InMemorySecretStore()
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            correlation_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="所有看板都必须使用真实数据，不能用 mock 数据。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.WORKING,
            content="用户要求产品看板使用真实数据，不能使用 mock 数据。",
            scopes=("all-apps",),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="create-memory",
        memory_id=memory_id,
    )
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="test-model",
            embedding_model="test-embedding",
            embedding_base_url="http://127.0.0.1:11434",
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profiles.activate(profile.profile_id, secrets)
    return governance, profiles, secrets


def _ndjson_stream(chunks: tuple[str, ...]) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["think"] is False
        body = "".join(
            json.dumps({"message": {"content": chunk}}, ensure_ascii=False) + "\n"
            for chunk in chunks
        )
        return httpx.Response(200, text=body)

    return httpx.MockTransport(respond)


def _collect(stream) -> list[dict]:
    async def drain() -> list[dict]:
        return [event async for event in stream]

    return asyncio.run(drain())


def test_streamed_answer_emits_recall_then_incremental_grounded_text(tmp_path) -> None:
    governance, profiles, secrets = _grounded_setup(tmp_path)
    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=_ndjson_stream(
            (
                '{"citations": ["memory-real-data"], ',
                '"answer": "你要求看板',
                '只展示真实数据。"}',
            )
        ),
    )

    events = _collect(answerer.answer_stream("我对 mock 数据的要求是什么？"))

    assert events[0]["type"] == "recall"
    assert [memory.memory_id for memory in events[0]["memories"]] == ["memory-real-data"]
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas == ["你要求看板", "只展示真实数据。"]
    final = events[-1]
    assert final["type"] == "final"
    assert final["mode"] == "generated"
    assert final["answer"] == "你要求看板只展示真实数据。"
    assert [memory.memory_id for memory in final["memories"]] == ["memory-real-data"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("change", ["set_scopes", "correct", "delete"])
def test_governance_change_during_generation_stops_the_old_answer(tmp_path, streaming, change):
    governance, profiles, secrets = _grounded_setup(tmp_path)

    def respond(request: httpx.Request) -> httpx.Response:
        memory = governance.get("memory-real-data", user_id="local")
        kwargs = {
            "user_id": "local",
            "request_id": "during-model",
            "expected_revision": memory.revision,
        }
        if change == "set_scopes":
            as_person(governance).set_scopes(memory.memory_id, scopes=("private",), **kwargs)
        elif change == "correct":
            as_person(governance).correct(
                memory.memory_id, content="The current decision is different", **kwargs
            )
        else:
            as_person(governance).delete(memory.memory_id, **kwargs)
        content = json.dumps({"citations": ["memory-real-data"], "answer": "旧版本答案"})
        return httpx.Response(200, json={"message": {"content": content}})

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=httpx.MockTransport(respond),
    )
    events = []

    async def run():
        if streaming:
            async for event in answerer.answer_stream("我对 mock 数据的要求是什么？"):
                events.append(event)
        else:
            await answerer.answer("我对 mock 数据的要求是什么？")

    with pytest.raises(MemoryAnswerError, match="memory_answer_context_changed"):
        asyncio.run(run())
    assert all(event["type"] == "recall" for event in events)


def test_streamed_deltas_always_reconstruct_the_final_answer(tmp_path) -> None:
    governance, profiles, secrets = _grounded_setup(tmp_path)

    def stream_for(chunks: tuple[str, ...]) -> list[dict]:
        answerer = MemoryAnswerer(
            governance=governance,
            profiles=profiles,
            secrets=secrets,
            transport=_ndjson_stream(chunks),
        )
        return _collect(answerer.answer_stream("我对 mock 数据的要求是什么？"))

    grounded = stream_for(('{"citations": ["memory-real-data"], "answer": "你要求真实数据。"}',))
    uncited = stream_for(('{"citations": [], "answer": "模型自己编的答案"}',))

    for events in (grounded, uncited):
        final = events[-1]
        streamed = "".join(event["text"] for event in events if event["type"] == "delta")
        assert streamed == final["answer"]

    # The uncited answer text must never have reached the reader.
    assert "模型自己编的答案" not in "".join(
        event["text"] for event in uncited if event["type"] == "delta"
    )
    assert uncited[-1]["mode"] == "empty"


def test_streamed_answer_withholds_text_when_citation_is_not_retrieved(tmp_path) -> None:
    governance, profiles, secrets = _grounded_setup(tmp_path)
    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=_ndjson_stream(
            (
                '{"citations": ["workspace-memory"], ',
                '"answer": "越权答案"}',
            )
        ),
    )

    events: list[dict] = []

    async def drain() -> None:
        async for event in answerer.answer_stream("我对 mock 数据的要求是什么？"):
            events.append(event)

    with pytest.raises(MemoryAnswerError, match="memory_answer_invalid_citation"):
        asyncio.run(drain())

    assert [event["type"] for event in events] == ["recall"]


def test_answer_uses_only_grounded_confirmed_memory_citations(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    profiles = ModelProfileStore(database)
    secrets = InMemorySecretStore()
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            correlation_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="所有看板都必须使用真实数据，不能用 mock 数据。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.WORKING,
            content="用户要求产品看板使用真实数据，不能使用 mock 数据。",
            scopes=("all-apps",),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="create-memory",
        memory_id="memory-real-data",
    )
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="test-model",
            embedding_model="test-embedding",
            embedding_base_url="http://127.0.0.1:11434",
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profiles.activate(profile.profile_id, secrets)

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "必须用“你”称呼用户" in body["messages"][0]["content"]
        assert body["messages"][1]["content"].find("memory-real-data") >= 0
        question = json.loads(body["messages"][1]["content"])["question"]
        model_answer = (
            {
                "answer": "现有记忆不足以回答这个问题。",
                "citations": [],
            }
            if question == "我下周的航班是什么？"
            else {
                "answer": "你要求看板只展示真实数据，不使用 mock 数据。",
                "citations": ["memory-real-data"],
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                model_answer,
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async def semantic_search(query: str) -> tuple[str, ...]:
        return () if "航班" in query else ("memory-real-data",)

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        semantic_search=semantic_search,
        transport=httpx.MockTransport(respond),
    )

    result = asyncio.run(answerer.answer("我有哪些不能妥协的原则？"))

    assert result.answer == "你要求看板只展示真实数据，不使用 mock 数据。"
    assert [item.memory_id for item in result.memories] == ["memory-real-data"]
    assert result.mode == "generated"

    refused = asyncio.run(answerer.answer("我下周的航班是什么？"))

    assert refused.answer == "现有记忆不足以回答这个问题。"
    assert refused.memories == ()
    assert refused.mode == "empty"


def test_answer_refuses_question_without_matching_confirmed_memory(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    answerer = MemoryAnswerer(
        governance=MemoryGovernanceStore(database),
        profiles=ModelProfileStore(database),
        secrets=InMemorySecretStore(),
    )

    result = asyncio.run(answerer.answer("我下周的航班是什么？"))

    assert result.answer == "现有记忆不足以回答这个问题。"
    assert result.memories == ()
    assert result.mode == "empty"


def test_answer_does_not_turn_a_failed_semantic_lane_into_no_grounding(tmp_path) -> None:
    governance, profiles, secrets = _grounded_setup(tmp_path)

    async def unavailable(_query: str) -> tuple[str, ...]:
        raise RuntimeError("semantic backend unavailable")

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        semantic_search=unavailable,
    )

    with pytest.raises(MemoryAnswerError, match="memory_answer_semantic_unavailable"):
        asyncio.run(answerer.answer("我下周的航班是什么？"))


def test_answer_keeps_lexical_hits_but_marks_failed_semantic_coverage(tmp_path) -> None:
    governance, profiles, secrets = _grounded_setup(tmp_path)

    async def unavailable(_query: str) -> tuple[str, ...]:
        raise RuntimeError("semantic backend unavailable")

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        semantic_search=unavailable,
        transport=_ndjson_stream(
            (
                '{"citations": ["memory-real-data"], ',
                '"answer": "你要求看板只展示真实数据。"}',
            )
        ),
    )

    events = _collect(answerer.answer_stream("我对 mock 数据的要求是什么？"))
    final = events[-1]
    assert final["mode"] == "generated"
    assert final["retrieval"]["semantic_state"] == "failed"


def test_default_qa_scope_excludes_confirmed_workspace_memory(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            workspace="workspace:v1:project",
            conversation_id="conversation",
            message_id="message",
            correlation_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="项目决定使用本地优先架构。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.WORKING,
            content=evidence.verbatim,
            scopes=("workspace:v1:project",),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="create-workspace-memory",
    )
    answerer = MemoryAnswerer(
        governance=governance,
        profiles=ModelProfileStore(database),
        secrets=InMemorySecretStore(),
    )

    result = asyncio.run(answerer.answer("项目采用什么架构？"))

    assert result.answer == "现有记忆不足以回答这个问题。"
    assert result.memories == ()
    assert result.mode == "empty"


def test_answer_rejects_model_citation_outside_retrieved_scope(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    profiles = ModelProfileStore(database)
    secrets = InMemorySecretStore()
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            correlation_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="我偏好简洁回答。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="用户偏好简洁回答。",
            scopes=("all-apps",),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="create",
        memory_id="allowed-memory",
    )
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="test-model",
            embedding_model="test-embedding",
            embedding_base_url="http://127.0.0.1:11434",
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profiles.activate(profile.profile_id, secrets)

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "answer": "越权答案",
                            "citations": ["workspace-memory"],
                        }
                    )
                }
            },
        )

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        semantic_search=lambda _query: asyncio.sleep(
            0,
            result=("allowed-memory",),
        ),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(MemoryAnswerError, match="memory_answer_invalid_citation"):
        asyncio.run(answerer.answer("我的回答偏好是什么？"))


def _compatible_gateway_setup(tmp_path):
    """A generator that works but does not accept response_format.

    必须是 openai_compatible。第一版这个 fixture 直接复用了 _grounded_setup 的
    Ollama 卡，而 Ollama 分支用的字段叫 format、不叫 response_format，于是
    「没带 response_format」这个断言恒真 —— 测试从没走进被测的那段代码。
    变异测试把它抓了出来。下面那句 assert 就是为了让这个前提自己说话。
    """
    governance, profiles, secrets = _grounded_setup(tmp_path)
    profile = profiles.create(
        ModelProfileInput(
            name="Compatible provider",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.invalid/standard/v1",
            model="deepseek-v4-pro",
            embedding_model="text-embedding",
            embedding_base_url="https://api.example.invalid/standard/v1",
            api_key="probe-key",
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=False,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    profiles.activate(profile.profile_id, secrets)
    active = profiles.active(secrets)
    assert active.provider_kind is ProviderKind.OPENAI_COMPATIBLE
    return governance, profiles, secrets


def test_a_model_that_cannot_be_schema_constrained_still_answers(tmp_path) -> None:
    """约束不了 schema 不等于回答不了。

    兼容网关可能对 response_format 返回 HTTP 400，而不带这个参数时模型仍可返回
    合法 JSON。之前这里把「约束不了」直接当成「不能用」，效果是在这类网关上
    「问记忆」永远只走原文摘录，永远拿不到生成的回答 —— 而模型明明能答。
    """
    governance, profiles, secrets = _compatible_gateway_setup(tmp_path)
    sent: list[bool] = []
    urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        sent.append(b"response_format" in request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "citations": ["memory-real-data"],
                                    "answer": "你要求看板只展示真实数据。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=httpx.MockTransport(respond),
    )
    result = asyncio.run(answerer.answer("看板用什么数据"))

    # 打过去了，打的是 chat/completions，而且没带那个网关不收的参数。
    # 三样都要断言：只断言最后一样时，一张走别的分支的卡也能让它恒真。
    assert urls == ["https://api.example.invalid/standard/v1/chat/completions"]
    assert sent == [False]
    assert result.mode == "generated"
    assert "真实数据" in result.answer


def test_no_model_at_all_still_falls_back_to_quoting(tmp_path) -> None:
    """正向对照：压根没有可用模型时，仍然只摘原文。

    没有这条，上一条可以被「无条件都去发生成请求」骗过。
    """
    governance, profiles, secrets = _grounded_setup(tmp_path)
    profile = profiles.active(secrets)
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=False,
            structured_output=False,
            embedding_available=True,
        ),
        secrets=secrets,
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("模型不可用时不该发出生成请求")

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=httpx.MockTransport(refuse),
    )
    result = asyncio.run(answerer.answer("看板用什么数据"))

    assert result.mode == "extractive"


def test_json_wrapped_in_a_code_fence_is_still_read(tmp_path) -> None:
    """不约束 schema 时，模型很常把 JSON 包在 ``` 里。

    严格的 json.loads 会把这种回答判成无效输出，于是「能答」白答一场。
    """
    governance, profiles, secrets = _compatible_gateway_setup(tmp_path)

    def respond(request: httpx.Request) -> httpx.Response:
        inner = json.dumps(
            {"citations": ["memory-real-data"], "answer": "你要求看板只展示真实数据。"},
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"```json\n{inner}\n```"}}]},
        )

    answerer = MemoryAnswerer(
        governance=governance,
        profiles=profiles,
        secrets=secrets,
        transport=httpx.MockTransport(respond),
    )
    result = asyncio.run(answerer.answer("看板用什么数据"))

    assert result.mode == "generated"
    assert "真实数据" in result.answer
