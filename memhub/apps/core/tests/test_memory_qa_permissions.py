import asyncio
import json
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest
from pydantic import SecretStr

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    GovernedMemory,
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
)
from one_memory.memory_qa import MemoryAnswer, MemoryAnswerer, MemoryAnswerError
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfile,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from tests.person_door import as_person

_QUESTION = ' \tSynthetic planning: preserve "Quoted" CASE?\n '
_ANSWER = "You prefer synthetic planning notes."
_SECRET = "synthetic-memory-qa-secret"
_PRIVATE_DETAIL = f"refused /synthetic/private/permission.sqlite3 credential={_SECRET}"
_DENIED = "memory_answer_generation_not_authorized"
type _Recheck = Callable[[], Awaitable[None]]
type _Responder = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


class _CountedSecrets(InMemorySecretStore):
    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline
        self.has_calls = 0
        self.resolve_calls = 0
        self.on_resolve: Callable[[], None] | None = None

    def has(self, secret_ref: str) -> bool:
        self.has_calls += 1
        # Keyring's has() resolves the secret, unlike InMemorySecretStore.has().
        return self.resolve(secret_ref) is not None

    def resolve(self, secret_ref: str) -> str | None:
        self.resolve_calls += 1
        self.timeline.append("secret")
        if self.on_resolve is not None:
            self.on_resolve()
        return super().resolve(secret_ref)


class _Authorization:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.calls: list[tuple[ModelProfile, str, tuple[GovernedMemory, ...]]] = []
        self.checks: dict[int, int] = {}
        self.revoked: set[int] = set()
        self.refuse_initial = False
        self.refuse_check: int | None = None
        self.error_type: type[Exception] = PermissionError

    async def authorize_generation(
        self,
        profile: ModelProfile,
        question: str,
        grounding: tuple[GovernedMemory, ...],
    ) -> _Recheck:
        lease_id = len(self.calls)
        self.calls.append((profile, question, grounding))
        self.timeline.append(f"authorize:{lease_id}")
        if self.refuse_initial:
            raise self.error_type(_PRIVATE_DETAIL)
        self.checks[lease_id] = 0

        async def recheck() -> None:
            self.checks[lease_id] += 1
            self.timeline.append(f"recheck:{lease_id}:{self.checks[lease_id]}")
            if lease_id in self.revoked or self.checks[lease_id] == self.refuse_check:
                raise self.error_type(_PRIVATE_DETAIL)

        return recheck


@dataclass
class _Setup:
    governance: MemoryGovernanceStore
    profiles: ModelProfileStore
    secrets: _CountedSecrets
    memories: tuple[GovernedMemory, ...]
    profile: ModelProfile
    timeline: list[str]
    requests: list[httpx.Request] = field(default_factory=list)
    citations: tuple[str, ...] = ("synthetic-memory-0",)
    split_answer: bool = True

    async def search(self, question: str) -> tuple[str, ...]:
        return tuple(memory.memory_id for memory in self.memories)

    def response(self, request: httpx.Request) -> httpx.Response:
        self.timeline.append("response")
        content = json.dumps({"citations": self.citations, "answer": _ANSWER})
        if not json.loads(request.content)["stream"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        split = content.index("synthetic planning") if self.split_answer else len(content)
        chunks = (content[:split], content[split:])
        body = "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) + "\n\n"
            for chunk in chunks
            if chunk
        )
        return httpx.Response(200, text=body + "data: [DONE]\n\n")

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.record_request(request)
        return self.response(request)

    def record_request(self, request: httpx.Request) -> None:
        assert str(request.url) == "https://memory-qa.example.invalid/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {_SECRET}"
        self.requests.append(request)
        self.timeline.append("request")

    def answerer(
        self, authorization: _Authorization, respond: _Responder | None = None
    ) -> MemoryAnswerer:
        return MemoryAnswerer(
            governance=self.governance,
            profiles=self.profiles,
            secrets=self.secrets,
            semantic_search=self.search,
            transport=httpx.MockTransport(respond or self.respond),
            authorize_generation=authorization.authorize_generation,
        )


def _setup(tmp_path: Path, *, model: str = "available") -> _Setup:
    database = tmp_path / "synthetic.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    profiles = ModelProfileStore(database)
    timeline: list[str] = []
    secrets = _CountedSecrets(timeline)
    memories = []
    for index in range(8):
        evidence = evidence_store.put(
            EvidenceInput(
                source_id="synthetic-qa",
                conversation_id="synthetic-conversation",
                message_id=f"synthetic-message-{index}",
                correlation_id=f"synthetic-message-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"Synthetic planning note {index}.",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        memory = as_person(governance).create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.WORKING,
                content=evidence.verbatim,
                scopes=("all-apps",),
                evidence_ids=(evidence.evidence_id,),
            ),
            request_id=f"create-{index}",
            memory_id=f"synthetic-memory-{index}",
        )
        memories.append(memory)
    profile = profiles.create(
        ModelProfileInput(
            name="Synthetic generation",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://memory-qa.example.invalid/v1",
            model="synthetic-generation",
            embedding_model="synthetic-embedding",
            embedding_base_url="https://memory-qa.example.invalid/v1",
            api_key=SecretStr(_SECRET),
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
    if model != "absent":
        profiles.activate(profile.profile_id, secrets)
    if model == "unavailable":
        profiles.record_capabilities(
            profile.profile_id,
            expected_revision=profile.revision,
            capabilities=ModelCapabilities(model_available=False),
            secrets=secrets,
        )
    profile = profiles.require(profile.profile_id, secrets)
    secrets.has_calls = secrets.resolve_calls = 0
    timeline.clear()
    return _Setup(governance, profiles, secrets, tuple(memories), profile, timeline)


async def _run(
    answerer: MemoryAnswerer,
    streaming: bool,
    events: list[dict[str, object]],
    question: str = _QUESTION,
) -> MemoryAnswer:
    if not streaming:
        return await answerer.answer(question)
    async for event in answerer.answer_stream(question):
        events.append(event)
    assert events and events[-1]["type"] == "final"
    return MemoryAnswer.model_validate(
        {key: events[-1][key] for key in ("answer", "memories", "mode")}
    )


def _assert_private_error(error: MemoryAnswerError) -> None:
    assert str(error) == _DENIED
    rendered = "".join(traceback.format_exception(error))
    assert _PRIVATE_DETAIL not in rendered
    assert _SECRET not in rendered
    assert error.__cause__ is None


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError, MemoryAnswerError])
def test_initial_refusal_precedes_all_secret_access_and_network(
    tmp_path: Path, streaming: bool, error_type: type[Exception]
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    authorization.refuse_initial = True
    authorization.error_type = error_type
    answerer = setup.answerer(authorization)
    events: list[dict[str, object]] = []

    with pytest.raises(MemoryAnswerError) as caught:
        asyncio.run(_run(answerer, streaming, events))

    _assert_private_error(caught.value)
    assert len(authorization.calls) == 1
    assert setup.secrets.has_calls == setup.secrets.resolve_calls == 0
    assert setup.requests == []
    assert all(event["type"] == "recall" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
def test_invalid_authorization_result_fails_before_secret_access(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)

    class InvalidAuthorization(_Authorization):
        async def authorize_generation(
            self, profile: ModelProfile, question: str, grounding: tuple[GovernedMemory, ...]
        ) -> _Recheck:
            return cast(_Recheck, None)

    answerer = setup.answerer(InvalidAuthorization(setup.timeline))
    with pytest.raises(MemoryAnswerError, match=f"^{_DENIED}$"):
        asyncio.run(_run(answerer, streaming, []))
    assert setup.secrets.has_calls == setup.secrets.resolve_calls == 0
    assert setup.requests == []


@pytest.mark.parametrize("streaming", [False, True])
def test_authorization_receives_exact_question_and_only_outbound_grounding(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    answerer = setup.answerer(authorization)
    events: list[dict[str, object]] = []

    result = asyncio.run(_run(answerer, streaming, events))

    assert len(authorization.calls) == len(setup.requests) == 1
    profile, question, grounding = authorization.calls[0]
    assert profile.profile_id == setup.profile.profile_id
    assert profile.revision == setup.profile.revision
    assert profile.has_api_key is False
    assert profile.credential_state == "unavailable"
    assert (profile.provider_kind, profile.base_url, profile.model) == (
        setup.profile.provider_kind,
        setup.profile.base_url,
        setup.profile.model,
    )
    assert question is _QUESTION
    assert grounding == setup.memories[:6]
    sent = json.loads(json.loads(setup.requests[0].content)["messages"][1]["content"])
    assert sent == {
        "question": _QUESTION,
        "memories": [
            {
                "memory_id": memory.memory_id,
                "content": memory.content,
                "kind": memory.kind,
                "updated_at": memory.updated_at.isoformat(),
            }
            for memory in grounding
        ],
    }
    assert setup.secrets.has_calls == 0
    assert setup.secrets.resolve_calls == 1
    assert setup.timeline.index("authorize:0") < setup.timeline.index("secret")
    assert setup.timeline[setup.timeline.index("request") - 1] == "recheck:0:1"
    assert setup.timeline[setup.timeline.index("response") + 1] == "recheck:0:2"
    assert authorization.checks[0] >= (5 if streaming else 3)
    assert result.mode == "generated"
    assert result.answer == _ANSWER
    assert result.memories == (setup.memories[0],)
    if streaming:
        assert events[0]["memories"] == setup.memories
        assert "".join(str(e["text"]) for e in events if e["type"] == "delta") == _ANSWER


@pytest.mark.parametrize("streaming", [False, True])
def test_revocation_while_obtaining_secret_prevents_the_request(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    setup.secrets.on_resolve = lambda: authorization.revoked.add(0)
    answerer = setup.answerer(authorization)
    events: list[dict[str, object]] = []

    with pytest.raises(MemoryAnswerError) as caught:
        asyncio.run(_run(answerer, streaming, events))

    _assert_private_error(caught.value)
    assert setup.secrets.resolve_calls == 1
    assert authorization.checks == {0: 1}
    assert setup.requests == []
    assert all(event["type"] == "recall" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
def test_revocation_during_delayed_response_withholds_generated_output(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    events: list[dict[str, object]] = []

    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            setup.record_request(request)
            started.set()
            await release.wait()
            return setup.response(request)

        task = asyncio.create_task(_run(setup.answerer(authorization, respond), streaming, events))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert len(setup.requests) == setup.secrets.resolve_calls == 1
            authorization.revoked.add(0)
            release.set()
            with pytest.raises(MemoryAnswerError) as caught:
                await task
            _assert_private_error(caught.value)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert len(setup.requests) == 1
    assert authorization.checks == {0: 2}
    assert all(event["type"] == "recall" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status", [200, 503])
def test_post_response_recheck_precedes_http_and_output_errors(
    tmp_path: Path, streaming: bool, status: int
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)

    def respond(request: httpx.Request) -> httpx.Response:
        setup.record_request(request)
        authorization.revoked.add(0)
        return httpx.Response(status, text="synthetic malformed model output")

    with pytest.raises(MemoryAnswerError) as caught:
        asyncio.run(_run(setup.answerer(authorization, respond), streaming, []))
    _assert_private_error(caught.value)
    assert len(setup.requests) == 1
    assert authorization.checks == {0: 2}


def test_answer_rechecks_again_immediately_before_publishing(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    authorization.refuse_check = 3

    with pytest.raises(MemoryAnswerError) as caught:
        asyncio.run(setup.answerer(authorization).answer(_QUESTION))

    _assert_private_error(caught.value)
    assert authorization.checks == {0: 3}
    assert len(setup.requests) == 1


@pytest.mark.parametrize("boundary", ["next_delta", "generated_final", "empty_final"])
def test_stream_rechecks_after_each_consumer_resume(tmp_path: Path, boundary: str) -> None:
    setup = _setup(tmp_path)
    setup.split_answer = boundary == "next_delta"
    if boundary == "empty_final":
        setup.citations = ()
    authorization = _Authorization(setup.timeline)
    answerer = setup.answerer(authorization)
    events: list[dict[str, object]] = []

    async def scenario() -> None:
        with pytest.raises(MemoryAnswerError) as caught:
            async for event in answerer.answer_stream(_QUESTION):
                events.append(event)
                if event["type"] == "delta":
                    authorization.revoked.add(0)
        _assert_private_error(caught.value)

    asyncio.run(scenario())
    assert [event["type"] for event in events] == ["recall", "delta"]
    assert len(setup.requests) == 1
    if boundary == "next_delta":
        assert events[-1]["text"] == "You prefer "
    elif boundary == "empty_final":
        assert _ANSWER not in str(events)


class _DelayedBody(httpx.AsyncByteStream):
    def __init__(self, content: bytes, started: asyncio.Event, release: asyncio.Event) -> None:
        self.content = content
        self.started = started
        self.release = release
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        await self.release.wait()
        yield self.content

    async def aclose(self) -> None:
        self.closed = True


def test_revocation_during_streamed_body_closes_response_without_publishing(
    tmp_path: Path,
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    events: list[dict[str, object]] = []

    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        body = _DelayedBody(b"", started, release)

        def respond(request: httpx.Request) -> httpx.Response:
            setup.record_request(request)
            body.content = setup.response(request).content
            return httpx.Response(200, stream=body)

        task = asyncio.create_task(_run(setup.answerer(authorization, respond), True, events))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert authorization.checks == {0: 2}
            authorization.revoked.add(0)
            release.set()
            with pytest.raises(MemoryAnswerError) as caught:
                await task
            _assert_private_error(caught.value)
            assert body.closed
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert len(setup.requests) == setup.secrets.resolve_calls == 1
    assert [event["type"] for event in events] == ["recall"]


def test_final_stream_publication_rechecks_after_response_has_closed(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.split_answer = False
    authorization = _Authorization(setup.timeline)
    events: list[dict[str, object]] = []

    async def scenario() -> None:
        release = asyncio.Event()
        release.set()
        body = _DelayedBody(b"", asyncio.Event(), release)

        def respond(request: httpx.Request) -> httpx.Response:
            setup.record_request(request)
            body.content = setup.response(request).content
            return httpx.Response(200, stream=body)

        def readable(memories: Sequence[GovernedMemory]) -> Sequence[GovernedMemory]:
            if body.closed:
                authorization.revoked.add(0)
            return memories

        answerer = setup.answerer(authorization, respond)
        answerer.filter_memories = readable
        with pytest.raises(MemoryAnswerError) as caught:
            await _run(answerer, True, events)
        _assert_private_error(caught.value)
        assert body.closed

    asyncio.run(scenario())
    assert len(setup.requests) == 1
    assert [event["type"] for event in events] == ["recall", "delta"]
    assert events[-1]["text"] == _ANSWER


@pytest.mark.parametrize("streaming", [False, True])
def test_per_answer_recheck_is_not_replaced_by_a_concurrent_answer(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)
    events: list[list[dict[str, object]]] = [[], []]

    async def scenario() -> None:
        started = (asyncio.Event(), asyncio.Event())
        release = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            setup.record_request(request)
            question = json.loads(json.loads(request.content)["messages"][1]["content"])["question"]
            started[0 if question == _QUESTION else 1].set()
            await release.wait()
            return setup.response(request)

        answerer = setup.answerer(authorization, respond)
        first = asyncio.create_task(_run(answerer, streaming, events[0]))
        tasks = [first]
        try:
            await asyncio.wait_for(started[0].wait(), timeout=5)
            second = asyncio.create_task(_run(answerer, streaming, events[1], "Synthetic followup"))
            tasks.append(second)
            await asyncio.wait_for(started[1].wait(), timeout=5)
            authorization.revoked.add(0)
            release.set()
            with pytest.raises(MemoryAnswerError) as caught:
                await first
            _assert_private_error(caught.value)
            assert (await second).mode == "generated"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert len(authorization.calls) == len(setup.requests) == 2
    assert authorization.checks[0] == 2
    assert authorization.checks[1] >= 3
    assert all(event["type"] == "recall" for event in events[0])


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("change", ["revision", "source"])
def test_generation_permission_does_not_replace_readability_checks(
    tmp_path: Path, streaming: bool, change: str
) -> None:
    setup = _setup(tmp_path)
    authorization = _Authorization(setup.timeline)

    def respond(request: httpx.Request) -> httpx.Response:
        if change == "revision":
            memory = setup.memories[0]
            as_person(setup.governance).correct(
                memory.memory_id,
                content="Synthetic corrected note.",
                user_id=memory.user_id,
                request_id="synthetic-correction",
                expected_revision=memory.revision,
            )
        else:
            answerer.filter_memories = lambda _memories: ()
        return setup.respond(request)

    answerer = setup.answerer(authorization, respond)
    events: list[dict[str, object]] = []
    with pytest.raises(MemoryAnswerError, match="^memory_answer_context_changed$"):
        asyncio.run(_run(answerer, streaming, events))
    assert len(setup.requests) == 1
    assert all(event["type"] == "recall" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
def test_permission_never_licenses_citations_outside_the_sent_grounding(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    setup.citations = (setup.memories[-1].memory_id,)
    authorization = _Authorization(setup.timeline)
    events: list[dict[str, object]] = []

    with pytest.raises(MemoryAnswerError, match="^memory_answer_invalid_citation$"):
        asyncio.run(_run(setup.answerer(authorization), streaming, events))

    assert len(setup.requests) == 1
    assert all(event["type"] == "recall" for event in events)


@pytest.mark.parametrize("streaming", [False, True])
def test_permission_does_not_turn_uncited_text_into_a_generated_answer(
    tmp_path: Path, streaming: bool
) -> None:
    setup = _setup(tmp_path)
    setup.citations = ()
    authorization = _Authorization(setup.timeline)
    events: list[dict[str, object]] = []

    result = asyncio.run(_run(setup.answerer(authorization), streaming, events))

    assert len(setup.requests) == 1
    assert result.mode == "empty"
    assert result.memories == ()
    assert _ANSWER not in result.answer
    assert _ANSWER not in str(events)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("model", ["absent", "unavailable"])
def test_local_extractive_answer_does_not_require_generation_consent(
    tmp_path: Path, streaming: bool, model: Literal["absent", "unavailable"]
) -> None:
    setup = _setup(tmp_path, model=model)
    authorization = _Authorization(setup.timeline)
    authorization.refuse_initial = True

    result = asyncio.run(_run(setup.answerer(authorization), streaming, []))

    assert result.mode == "extractive"
    assert result.memories == setup.memories[:3]
    assert authorization.calls == []
    assert setup.requests == []
    assert setup.secrets.has_calls == setup.secrets.resolve_calls == 0


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("phase", ["initial", "recheck"])
def test_cancelling_authorization_stops_without_network_or_generated_success(
    tmp_path: Path, streaming: bool, phase: str
) -> None:
    setup = _setup(tmp_path)
    events: list[dict[str, object]] = []

    async def scenario() -> None:
        started = asyncio.Event()

        class CancellableAuthorization(_Authorization):
            async def authorize_generation(
                self, profile: ModelProfile, question: str, grounding: tuple[GovernedMemory, ...]
            ) -> _Recheck:
                async def recheck() -> None:
                    started.set()
                    await asyncio.Event().wait()

                if phase == "initial":
                    await recheck()
                return recheck

        answerer = setup.answerer(CancellableAuthorization(setup.timeline))
        task = asyncio.create_task(_run(answerer, streaming, events))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert setup.requests == []
    assert setup.secrets.resolve_calls == (0 if phase == "initial" else 1)
    assert all(event["type"] == "recall" for event in events)
