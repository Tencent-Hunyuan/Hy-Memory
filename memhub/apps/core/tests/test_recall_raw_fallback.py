"""MemHub_recall degrades to the imported conversations instead of answering empty.

Without a usable generation model no memory can be distilled, and with no
memory in the library a memory search can only come back empty. In both cases
recall searches the SDK raw layer and says so with `mode: "raw"`. External
callers need the conversations tool and all-apps scope; Core resolves that to
every connected, unrevoked source.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_sdk_types import HySdkRawHit
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.recall import RecallHit, RecallReceipt, RecallRequest, RecallState
from one_memory.recall_fallback import recall_with_fallback
from one_memory.secrets import InMemorySecretStore

AT = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())


def _raw(raw_id, content, *, source="cursor", score=0.8):
    return HySdkRawHit(
        raw_id=raw_id,
        content=content,
        user_id="local",
        agent_id="a",
        session_id=f"s-{raw_id}",
        source=source,
        memory_at=AT,
        score=score,
    )


def _memory_receipt(request, *hits):
    return RecallReceipt(
        state=RecallState.GROUNDED if hits else RecallState.EMPTY,
        query=request.query,
        scope=request.scope,
        hits=hits,
        used_characters=sum(len(hit.content) for hit in hits),
        truncated=False,
    )


def _memory_hit():
    return RecallHit(
        memory_id="m1",
        content="The user prefers tea.",
        kind="l2_fact",
        scopes=(),
        evidence_ids=(),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        score=0.9,
        content_format="hy-sdk",
    )


def _fakes(*, ready, memories, has_memories, raw, sources=None):
    calls = {"memory": 0, "raw": 0, "library": 0, "sources": None}

    async def model_ready():
        return ready

    async def recall_memories(request):
        calls["memory"] += 1
        return _memory_receipt(request, *memories)

    async def library():
        calls["library"] += 1
        return has_memories

    async def raw_sources():
        return sources

    async def search_raw(query, limit, allowed):
        calls["raw"] += 1
        calls["sources"] = allowed
        return tuple(raw)

    return calls, {
        "model_ready": model_ready,
        "recall_memories": recall_memories,
        "has_memories": library,
        "raw_sources": raw_sources,
        "search_raw": search_raw,
    }


def run(request, fakes):
    return asyncio.run(recall_with_fallback(request, **fakes))


def test_with_a_model_and_memories_recall_searches_memories_only():
    calls, fakes = _fakes(ready=True, memories=[_memory_hit()], has_memories=True, raw=[])

    receipt = run(RecallRequest(query="tea"), fakes)

    assert receipt.mode == "memory" and receipt.fallback_reason is None
    assert [hit.memory_id for hit in receipt.hits] == ["m1"]
    assert calls["raw"] == 0 and calls["library"] == 0


def test_without_a_model_recall_searches_the_raw_layer():
    calls, fakes = _fakes(
        ready=False, memories=[], has_memories=True, raw=[_raw("r1", "我喜欢喝茶")]
    )

    receipt = run(RecallRequest(query="tea"), fakes)

    assert calls["memory"] == 0
    assert receipt.mode == "raw" and receipt.fallback_reason == "model_unavailable"
    assert receipt.state == RecallState.GROUNDED
    hit = receipt.hits[0]
    assert (hit.memory_id, hit.kind, hit.content_format) == ("r1", "raw", "raw")
    assert (hit.source, hit.session_id) == ("cursor", "s-r1")
    assert hit.updated_at == datetime(2026, 9, 1, tzinfo=UTC)


def test_with_no_memories_yet_recall_searches_the_raw_layer():
    calls, fakes = _fakes(ready=True, memories=[], has_memories=False, raw=[_raw("r1", "x")])

    receipt = run(RecallRequest(query="tea"), fakes)

    assert calls["memory"] == 1 and calls["library"] == 1
    assert receipt.mode == "raw" and receipt.fallback_reason == "no_memories"


def test_with_memories_but_none_relevant_the_answer_stays_an_empty_memory_result():
    calls, fakes = _fakes(ready=True, memories=[], has_memories=True, raw=[_raw("r1", "x")])

    receipt = run(RecallRequest(query="tea"), fakes)

    assert receipt.mode == "memory" and receipt.hits == ()
    assert calls["raw"] == 0


def test_raw_fallback_respects_the_callers_raw_sources():
    calls, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw("r1", "a", source="cursor"), _raw("r2", "b", source="codex")],
        sources=frozenset({"cursor"}),
    )

    receipt = run(RecallRequest(query="q"), fakes)

    assert calls["sources"] == frozenset({"cursor"})
    assert [hit.memory_id for hit in receipt.hits] == ["r1"]


def test_raw_fallback_rechecks_sources_after_search_before_releasing_content():
    _, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw("r1", "private", source="cursor")],
        sources=frozenset({"cursor"}),
    )
    checks = iter((frozenset({"cursor"}), frozenset()))

    async def sources():
        return next(checks)

    fakes["raw_sources"] = sources
    receipt = run(RecallRequest(query="private"), fakes)

    assert receipt.state == RecallState.EMPTY
    assert receipt.hits == ()


def test_a_caller_with_no_raw_grant_gets_nothing_rather_than_a_wider_read():
    calls, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw("r1", "a")],
        sources=frozenset(),
    )

    receipt = run(RecallRequest(query="q"), fakes)

    assert calls["raw"] == 0
    assert receipt.mode == "raw" and receipt.hits == () and receipt.state == RecallState.EMPTY


def test_raw_fallback_keeps_the_count_and_character_budgets():
    _, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw(f"r{index}", "x" * 150) for index in range(5)],
    )

    by_count = run(RecallRequest(query="q", limit=2), fakes)
    by_chars = run(RecallRequest(query="q", limit=10, character_budget=300), fakes)

    assert len(by_count.hits) == 2 and by_count.truncated_by.value == "count_budget"
    assert len(by_chars.hits) == 2 and by_chars.truncated_by.value == "character_budget"
    assert by_chars.used_characters == 300


def test_oversized_raw_hit_returns_a_query_anchored_excerpt_instead_of_empty():
    _, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw("large", ("x" * 5_000) + "WebGL 黄色节点" + ("y" * 5_000))],
    )

    receipt = run(
        RecallRequest(query="WebGL 黄色节点", limit=3, character_budget=300),
        fakes,
    )

    assert receipt.state == RecallState.GROUNDED
    assert len(receipt.hits) == 1
    assert len(receipt.hits[0].content) == receipt.used_characters == 300
    assert "WebGL 黄色节点" in receipt.hits[0].content
    assert receipt.truncated_by.value == "character_budget"


@pytest.mark.parametrize("prefix_length", [0, 100, 5_000, 9_980])
def test_raw_excerpt_never_exceeds_its_character_budget(prefix_length):
    needle = "WebGL"
    content = ("x" * prefix_length) + needle + ("y" * (10_000 - prefix_length))
    _, fakes = _fakes(
        ready=False,
        memories=[],
        has_memories=False,
        raw=[_raw("large", content)],
    )

    receipt = run(RecallRequest(query=needle, character_budget=300), fakes)

    assert len(receipt.hits[0].content) == receipt.used_characters == 300
    assert needle in receipt.hits[0].content


def test_the_probe_never_falls_back():
    calls, fakes = _fakes(ready=False, memories=[], has_memories=False, raw=[_raw("r1", "x")])

    receipt = run(RecallRequest(query="__om_probe__"), fakes)

    assert calls["memory"] == 1 and calls["raw"] == 0
    assert receipt.mode == "memory"


EXTERNAL = {
    "X-One-Memory-Read-Context": "external",
    "X-One-Memory-Installation-Id": "24361b44-5a15-4f87-ad09-05571dc281c3",
    "X-One-Memory-Installation-Revision": "1",
}


@pytest.mark.fresh_sdk
def test_recall_route_falls_back_and_keeps_the_raw_grant(tmp_path, monkeypatch):
    searched: list[object] = []

    async def no_model(_app):
        return False

    async def raw(_app, query, *, limit, sources, user_id="local"):
        searched.append(sources)
        return (_raw("r1", "上周决定用 Postgres", source="cursor"),)

    monkeypatch.setattr(api, "_recall_model_ready", no_model)
    monkeypatch.setattr(api, "_search_sdk_raw", raw)

    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("recall-test"),
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer recall-test"},
            ) as client,
        ):
            cursor_root = tmp_path / "cursor"
            cursor_root.mkdir()
            app.state.source_grant_store.grant(
                user_id="local",
                source_id="cursor",
                root=cursor_root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            local = await client.post("/api/v1/recall", json={"query": "数据库"})
            recall_only = await client.post(
                "/api/v1/recall",
                json={"query": "数据库"},
                headers={
                    **EXTERNAL,
                    "X-One-Memory-Allowed-Tools": json.dumps(["om_recall"]),
                    "X-One-Memory-Allowed-Scopes": json.dumps(["all-apps"]),
                },
            )
            cross_app = await client.post(
                "/api/v1/recall",
                json={"query": "数据库"},
                headers={
                    **EXTERNAL,
                    "X-One-Memory-Allowed-Tools": json.dumps(["om_recall", "om_conversations"]),
                    "X-One-Memory-Allowed-Scopes": json.dumps(["all-apps", "source:workbuddy"]),
                },
            )
            return local.json(), recall_only.json(), cross_app.json()

    local, recall_only, cross_app = asyncio.run(scenario())

    assert local["mode"] == "raw" and local["fallback_reason"] == "model_unavailable"
    assert local["hits"][0]["content_format"] == "raw"
    assert local["hits"][0]["source"] == "cursor"
    # A trusted local caller reads every source.
    assert searched[0] is None
    # A host granted recall alone may not read original conversations.
    assert recall_only["mode"] == "raw" and recall_only["hits"] == []
    # all-apps covers every connected source, not only the host's source scope.
    assert cross_app["hits"][0]["source"] == "cursor"
    assert searched == [None, frozenset({"cursor"})]


def test_the_total_recall_budget_stays_under_the_brokers_wait():
    from one_memory.recall_fallback import recall_total_timeout_seconds

    assert recall_total_timeout_seconds(800) == pytest.approx(3.8)
    # The broker gives Core 15 seconds; Core answers before it gives up.
    assert recall_total_timeout_seconds(60_000) == 12.0


@pytest.mark.fresh_sdk
def test_a_recall_that_cannot_finish_in_time_is_a_clear_timeout(tmp_path, monkeypatch):
    """A recall stuck behind the SDK answers 504 `recall_timeout`, not nothing (#231)."""
    from one_memory import recall_fallback

    async def stuck(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(recall_fallback, "recall_with_fallback", stuck)
    monkeypatch.setattr(recall_fallback, "recall_total_timeout_seconds", lambda _ms: 0.2)

    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("recall-test"),
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer recall-test"},
            ) as client,
        ):
            return await asyncio.wait_for(
                client.post("/api/v1/recall", json={"query": "数据库"}), 5
            )

    response = asyncio.run(scenario())

    assert response.status_code == 504
    assert response.json()["detail"] == "recall_timeout"
