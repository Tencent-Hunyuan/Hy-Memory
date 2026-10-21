"""MemHub_context memory lane: HY decides the order, keywords only when HY does not answer.

Same decision as host recall (用户 09-24 13:50). Absence assertions are preceded
by the same read returning the memory while it was still readable.
"""

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from one_memory.context_memory import read_memory_context
from one_memory.context_models import ContextSearchRequest
from one_memory.hy_memory_bridge import _joined_thread
from one_memory.semantic_deadline import SemanticMemoryHit, search_within
from tests.test_context_api import AUTH, URL
from tests.test_context_api import app as app  # noqa: F401
from tests.test_context_memory import database as database  # noqa: F401
from tests.test_context_memory import put


def _read(database, semantic_ids=(), limit=8, **kwargs):
    return read_memory_context(
        database,
        ContextSearchRequest(query="alpha beta gamma", kinds=("memory",), limit=limit),
        scopes=None,
        deadline=time.monotonic() + 5,
        semantic_ids=semantic_ids,
        **kwargs,
    )


def _ids(result):
    return [item.memory_id for item in result.items]


def test_hy_order_leads_even_when_the_hy_hit_also_matches_keywords(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hy-first", content="alpha only")
        put(db, "keyword-strong", content="alpha beta gamma")
    result = _read(database, semantic_ids=("hy-first", "keyword-strong"))
    assert _ids(result) == ["hy-first", "keyword-strong"]


def test_keyword_only_hits_are_interleaved_with_hy_hits(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hy-one", content="unrelated words")
        put(db, "hy-two", content="other words")
        put(db, "keyword-only", content="alpha beta gamma")
    result = _read(database, semantic_ids=("hy-one", "hy-two"))
    assert _ids(result) == ["hy-one", "keyword-only", "hy-two"]


def test_keyword_only_hits_keep_a_slot_when_many_hy_hits_are_returned(database):
    gone = [f"gone-{index}" for index in range(3)]
    kept = [f"hy-{index:02}" for index in range(13)]
    with closing(sqlite3.connect(database)) as db, db:
        for memory_id in gone + kept:
            put(db, memory_id, content="unrelated words")
        put(db, "keyword-only", content="alpha beta gamma")
        db.execute(
            "UPDATE governed_memories SET status='deleted', deleted_at=updated_at "
            "WHERE memory_id LIKE 'gone-%'"
        )
    result = _read(database, semantic_ids=(*gone, *kept), limit=20)
    assert _ids(result) == [kept[0], "keyword-only", *kept[1:]]


def test_a_repeated_hy_id_appears_once_at_its_first_position(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hy-keyword", content="alpha beta gamma")
        put(db, "hy-only", content="unrelated words")
    result = _read(database, semantic_ids=("hy-keyword", "hy-only", "hy-keyword", "hy-only"))
    assert _ids(result) == ["hy-keyword", "hy-only"]


def test_context_uses_sdk_formatted_memory_content(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hy-formatted", content="alpha beta gamma")
    formatted = "Memory: alpha beta gamma\nWhen: 2026-09-12 08:00:00 UTC"

    result = _read(
        database,
        semantic_hits=(
            SemanticMemoryHit(
                memory_id="hy-formatted",
                score=0.9,
                formatted_content=formatted,
                sdk_formatted=True,
                formatting_complete=True,
            ),
        ),
    )

    item = next(item for item in result.items if item.memory_id == "hy-formatted")
    assert item.content == formatted


@pytest.mark.parametrize(
    "answer", ("hy-memory-id", ["hy-one", 3], {"hy-b", "hy-a"}, {"hy-a": 1}, iter(["hy-a"]))
)
def test_a_malformed_hy_answer_counts_as_hy_not_answering(answer):
    async def malformed(_query: str):
        return answer

    assert asyncio.run(search_within(malformed, "q", 1.0)) == ((), "failed")


@pytest.mark.parametrize("answer", (["hy-a", "hy-b"], ("hy-a", "hy-b")))
def test_a_list_or_tuple_of_ids_is_hy_answering(answer):
    async def answered(_query: str):
        return answer

    assert asyncio.run(search_within(answered, "q", 1.0)) == (
        (
            SemanticMemoryHit("hy-a", 1.0),
            SemanticMemoryHit("hy-b", 0.5),
        ),
        "answered",
    )


def test_score_pairs_are_hy_answering_too():
    async def answered(_query: str):
        return [("hy-a", 0.9), ("hy-b", 0.8)]

    assert asyncio.run(search_within(answered, "q", 1.0)) == (
        (
            SemanticMemoryHit("hy-a", 0.9),
            SemanticMemoryHit("hy-b", 0.8),
        ),
        "answered",
    )


def test_hy_that_cancels_itself_counts_as_hy_not_answering():
    async def self_cancelling(_query: str):
        raise asyncio.CancelledError

    assert asyncio.run(search_within(self_cancelling, "q", 1.0)) == ((), "failed")


def test_context_lane_filters_a_deleted_hy_ranked_memory(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "doomed", content="unrelated words")
        put(db, "kept", content="other words")
    assert _ids(_read(database, semantic_ids=("doomed", "kept"))) == ["doomed", "kept"]
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE governed_memories SET status='deleted', deleted_at=updated_at "
            "WHERE memory_id='doomed'"
        )
    assert _ids(_read(database, semantic_ids=("doomed", "kept"))) == ["kept"]


def test_hy_that_answered_is_not_reported_as_lexical_only(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "hy", content="unrelated words")
    result = _read(database, semantic_ids=("hy",), semantic_outcome="answered")
    assert (result.status, result.reason) == ("ok", None)
    assert result.searched_channels == ("lexical", "semantic")
    assert result.semantic_status == "completed"
    assert result.index_complete is None
    assert not result.timed_out


def test_hy_that_did_not_answer_marks_the_lane_degraded(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "keyword", content="alpha beta gamma")
    for outcome in ("failed", "timed_out"):
        result = _read(database, semantic_outcome=outcome)
        assert _ids(result) == ["keyword"]
        assert (result.status, result.reason) == ("degraded", "lexical_only")
        assert result.searched_channels == ("lexical", "semantic")
        assert result.semantic_status == outcome
        assert result.timed_out is (outcome == "timed_out")


def _post_timed(application, body, semantic_search):
    application.state.recall_target = SimpleNamespace(semantic_search=semantic_search)

    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            started = time.monotonic()
            response = await client.post(URL, json=body, headers=AUTH)
            return response, time.monotonic() - started

    return asyncio.run(run())


def test_hy_failure_is_visible_on_the_memory_path(app):  # noqa: F811
    async def broken(_query: str) -> tuple[str, ...]:
        raise RuntimeError("hy down")

    response, _elapsed = _post_timed(app, {"query": "needle", "kinds": ["memory"]}, broken)
    body = response.json()
    assert [item["memory_id"] for item in body["items"]] == ["memory"], body
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"
    assert body["state"] == "partial"


def test_hy_that_refuses_cancellation_cannot_hold_the_memory_path(app):  # noqa: F811
    async def uncancellable(_query: str) -> tuple[str, ...]:
        await _joined_thread(time.sleep, 3)
        return ()

    response, elapsed = _post_timed(
        app, {"query": "needle", "kinds": ["memory"], "timeout_ms": 500}, uncancellable
    )
    body = response.json()
    assert elapsed < 0.7
    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"
    assert "timeout_budget" not in body["paths"]["memory"]["truncated_by"]


def test_slow_local_memory_ledger_cannot_exceed_the_lane_budget(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    original = context_routes.read_memory_context

    def slow(*args, **kwargs):
        time.sleep(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(context_routes, "read_memory_context", slow)
    response, elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["raw", "memory"], "timeout_ms": 3000},
        None,
    )
    assert elapsed < 1.0
    body = response.json()
    assert body["paths"]["memory"]["status"] == "unavailable"
    assert body["paths"]["memory"]["reason"] == "timeout_budget"


def test_slow_raw_lane_leaves_time_to_revalidate_a_completed_memory_lane(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    original = context_routes.read_raw_indexed_context

    def slow(*args, **kwargs):
        time.sleep(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(context_routes, "read_raw_indexed_context", slow)
    response, elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["raw", "memory"], "timeout_ms": 3000},
        None,
    )
    body = response.json()

    assert elapsed < 0.9
    assert body["paths"]["raw"]["reason"] == "timeout_budget"
    assert body["paths"]["memory"]["returned_count"] == 1
    assert body["paths"]["memory"]["reason"] != "timeout_budget"


def test_default_recall_returns_existing_memory_within_one_second_when_hy_is_slow(
    app,
):  # noqa: F811
    async def slow(_query: str) -> tuple[str, ...]:
        await _joined_thread(time.sleep, 3)
        return ()

    response, elapsed = _post_timed(app, {"query": "needle"}, slow)
    body = response.json()
    assert elapsed < 1.0
    assert [item["memory_id"] for item in body["items"] if item["kind"] == "memory"] == ["memory"]
    assert body["state"] == "partial"
    assert body["paths"]["memory"]["reason"] == "lexical_only"
    assert "timeout_budget" not in body["paths"]["memory"]["truncated_by"]


def test_completed_lexical_memory_is_returned_when_semantic_enrichment_runs_out_of_budget(
    app, monkeypatch
):  # noqa: F811
    from one_memory import context_routes

    original = context_routes.read_memory_context

    def delayed_enrichment(*args, **kwargs):
        if kwargs.get("semantic_outcome") == "answered":
            time.sleep(0.6)
        return original(*args, **kwargs)

    async def semantic(_query: str) -> tuple[str, ...]:
        return ("memory",)

    _post_timed(app, {"query": "needle", "kinds": ["memory"]}, None)
    monkeypatch.setattr(context_routes, "read_memory_context", delayed_enrichment)
    response, elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"], "timeout_ms": 600},
        semantic,
    )
    body = response.json()

    assert elapsed < 0.7
    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"
    assert "timeout_budget" not in body["paths"]["memory"]["truncated_by"]


def test_ready_semantic_client_cannot_discard_a_completed_lexical_result(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    original = context_routes.read_memory_context

    def broken_enrichment(*args, **kwargs):
        if kwargs.get("semantic_outcome") == "answered":
            raise ValueError("synthetic enriched read failure")
        return original(*args, **kwargs)

    async def semantic(_query: str) -> tuple[str, ...]:
        return ("memory",)

    monkeypatch.setattr(context_routes, "read_memory_context", broken_enrichment)
    response, _elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"]},
        semantic,
    )
    body = response.json()

    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"


def test_answered_semantic_search_with_no_ids_keeps_lexical_memory(app):  # noqa: F811
    async def answered_empty(_query: str) -> tuple[str, ...]:
        return ()

    response, _elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"]},
        answered_empty,
    )
    body = response.json()

    assert response.status_code == 200
    assert body["state"] == "grounded"
    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["paths"]["memory"]["status"] == "ok"


def test_context_route_keeps_unique_lexical_hit_ahead_of_full_semantic_top_k(
    app,
):  # noqa: F811
    database = app.state.evidence_store.database_path
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "unique-target", content="用户使用 route-exact-marker 作为验收代号。")
        semantic_ids = []
        for index in range(10):
            memory_id = f"route-semantic-{index}"
            semantic_ids.append(memory_id)
            put(db, memory_id, content=f"无关语义候选 {index}。")

    async def semantic(_query: str) -> tuple[str, ...]:
        return tuple(semantic_ids)

    response, _elapsed = _post_timed(
        app,
        {"query": "route-exact-marker", "kinds": ["memory"], "limit": 8},
        semantic,
    )
    memories = [item for item in response.json()["items"] if item["kind"] == "memory"]
    target = next(item for item in memories if item["memory_id"] == "unique-target")
    assert target["matched_by"] == ["lexical"]
    assert any("governed_semantic" in item["matched_by"] for item in memories)


def test_ready_semantic_result_survives_a_concurrent_lexical_read_failure(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    original = context_routes.read_memory_context

    def broken_lexical(*args, **kwargs):
        if kwargs.get("semantic_outcome") == "absent":
            raise ValueError("synthetic lexical read failure")
        return original(*args, **kwargs)

    async def semantic(_query: str) -> tuple[str, ...]:
        return ("memory",)

    monkeypatch.setattr(context_routes, "read_memory_context", broken_lexical)
    response, _elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"]},
        semantic,
    )
    body = response.json()

    assert [item["memory_id"] for item in body["items"]] == ["memory"], body
    assert body["paths"]["memory"]["status"] == "ok"
    assert body["paths"]["memory"]["reason"] is None


def test_unavailable_semantic_result_cannot_discard_lexical_memory(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes
    from one_memory.context_models import ContextLaneResult

    original = context_routes.read_memory_context

    def unavailable_enrichment(*args, **kwargs):
        if kwargs.get("semantic_outcome") == "answered":
            return ContextLaneResult(status="unavailable", reason="read_unavailable")
        return original(*args, **kwargs)

    async def semantic(_query: str) -> tuple[str, ...]:
        return ("memory",)

    monkeypatch.setattr(context_routes, "read_memory_context", unavailable_enrichment)
    response, _elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"]},
        semantic,
    )
    body = response.json()

    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"


def test_context_diagnostics_report_each_lane_without_changing_v1(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    events = []
    monkeypatch.setattr(context_routes, "diagnostic_event", lambda **values: events.append(values))

    async def answered(_query: str) -> tuple[str, ...]:
        return ("memory",)

    response, _elapsed = _post_timed(app, {"query": "needle"}, answered)
    assert response.status_code == 200
    assert set(response.json()["paths"]["memory"]) == {
        "status",
        "reason",
        "elapsed_ms",
        "returned_count",
        "truncated",
        "truncated_by",
    }
    lanes = {
        event["details"]["operation_kind"]: event
        for event in events
        if event["event"] == "context_lane.completed"
    }
    assert lanes["raw"]["details"] == {
        "operation_kind": "raw",
        "mode": "lexical",
        "status": "ok",
        "index_complete": False,
        "reason": "lexical_only",
        "truncated": False,
        "timed_out": False,
    }
    assert lanes["memory"]["details"] == {
        "operation_kind": "memory",
        "mode": "lexical,semantic",
        "status": "ok",
        "index_complete": None,
        "lexical_hits": 1,
        "semantic_hits": 1,
        "reason": None,
        "truncated": False,
        "timed_out": False,
    }
    stages = {
        event["details"]["phase"] for event in events if event["event"] == "context_stage.completed"
    }
    assert stages == {
        "raw_ms",
        "memory_ms",
        "raw_revalidation_ms",
        "memory_revalidation_ms",
        "fusion_ms",
        "total_ms",
        "serialization_ms",
    }


def test_read_unavailable_diagnostic_names_failure_site_and_exception_class(app, monkeypatch):  # noqa: F811
    from one_memory import context_routes

    events = []
    monkeypatch.setattr(context_routes, "diagnostic_event", lambda **values: events.append(values))

    def broken_reader(*_args, **_kwargs):
        raise ValueError("private memory content must not be logged")

    monkeypatch.setattr(context_routes, "read_memory_context", broken_reader)
    response, _elapsed = _post_timed(
        app,
        {"query": "needle", "kinds": ["memory"]},
        None,
    )

    assert response.json()["paths"]["memory"]["reason"] == "read_unavailable"
    event = next(
        item
        for item in events
        if item["event"] == "context_lane.completed"
        and item["details"]["operation_kind"] == "memory"
    )
    assert event["details"]["failure_site"] == "memory_lexical"
    assert event["details"]["error_class"] == "ValueError"
    assert "private memory content" not in json.dumps(event)
