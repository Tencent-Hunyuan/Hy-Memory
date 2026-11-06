"""The SDK surface MemHub exposes: search pass-through, layers, raw, v2 aliases.

P2-1 (R8): `context/search`, `recall` and the v2 ask routes pass the public
`client.search` knobs through, validated, and keep a caller's floor on what
they return. P2-2 (R9): the library honours `layers`, `statuses`, `scene` and
the source/session/agent filters, and counts memories per layer. P2-4: raw
lists the SDK's L1 raw turns, active and shadow. P2-3: v2 aliases of the
source and import routes, per user. Profile versions read the L0 chain.

Every route stays scoped to its one `user_id`; the stand-in client records
each call so a test can see exactly what reached the SDK.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.secrets import InMemorySecretStore

TOKEN = "sdk-surface-test"

CHAT_ROWS = {
    "local": [
        ("m-high", "local likes green tea", 0.95, "l2_fact", "hermes"),
        ("m-mid", "local walks at dawn", 0.6, "l2_fact", "codex"),
    ],
    "wife": [("w-high", "wife likes coffee", 0.92, "l2_fact", "hermes")],
}
RAW_ROWS = {
    "local": [
        {
            "memory_id": "raw-1",
            "content": (
                "[user]: 我叫费天翔\n[assistant]: 好的，记住了。"
                "\n[user]: 我喜欢绿茶\n[assistant]: 也记住了。"
            ),
            "status": "shadow",
            "session_id": "s-1",
            "source": "hermes",
            "memory_at": 1_790_000_000,
            "gmt_created": 1_790_000_100,
        },
        {
            "memory_id": "raw-2",
            "content": "[user]: 你好",
            "status": "active",
            "session_id": "s-2",
            "source": "codex",
            "memory_at": 1_790_100_000,
            "gmt_created": 1_790_100_100,
        },
    ],
    "wife": [
        {
            "memory_id": "raw-w",
            "content": "[user]: I am the wife",
            "status": "active",
            "session_id": "s-w",
            "source": "hermes",
            "memory_at": 1_790_000_000,
            "gmt_created": 1_790_000_000,
        }
    ],
}


def _chat(user_id: str, memory_id: str, content: str, layer: str, source: str) -> dict:
    return {
        "memory_id": memory_id,
        "content": content,
        "layer": layer,
        "status": "active",
        "user_id": user_id,
        "agent_id": "all-apps",
        "session_id": "s-1",
        "source": source,
        "tags": [],
        "gmt_created": 1_790_000_000,
    }


#: The keyword parameters of the pinned `HyMemoryClient.search`, which takes
#: no **kwargs. `test_pinned_sdk_raw_search` checks this set against the
#: installed wheel, so the stand-in below rejects what the release rejects.
SDK_SEARCH_PARAMETERS = frozenset(
    {
        "queries",
        "scene",
        "search_mode",
        "user_ids",
        "agent_ids",
        "session_ids",
        "include_sources",
        "exclude_sources",
        "limit",
        "min_score",
        "working_fact_min_score",
        "working_experience_min_score",
        "schema_min_score",
        "schema_limit",
        "intention_limit",
        "created_after",
        "reader",
        "workspace_id",
        "branch",
        "request_id",
        "layers",
        "statuses",
        "embed_override",
        "query_embedding",
        "rerank",
    }
)


class RecordingSdk:
    """The public client surface, recording every call's keyword arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, dict(kwargs)))

    def last(self, name: str) -> dict:
        return [kwargs for called, kwargs in self.calls if called == name][-1]

    def all(self, name: str) -> list[dict]:
        return [kwargs for called, kwargs in self.calls if called == name]

    # The SDK answers rows below a caller's floor in some lanes (a schema
    # floor, a fused rank score), so Core must filter what it returns.
    def search(self, query, **kwargs):
        unknown = set(kwargs) - SDK_SEARCH_PARAMETERS
        if unknown:
            # The pinned `HyMemoryClient.search` takes no **kwargs.
            raise TypeError(f"search() got unexpected keyword arguments {sorted(unknown)}")
        self._record("search", {"query": query, **kwargs})
        [user_id] = kwargs["user_ids"]
        if kwargs.get("layers") == ["l1_raw"]:
            hits = [
                {**row, "user_id": user_id, "agent_id": "all-apps", "score": score}
                for row, score in zip(RAW_ROWS.get(user_id, []), (0.97, 0.3), strict=False)
            ]
            return {"request_id": "s", "memories": hits}
        rows = [
            {**_chat(user_id, mid, text, layer, source), "score": score}
            for mid, text, score, layer, source in CHAT_ROWS.get(user_id, [])
        ]
        scene = kwargs.get("scene")
        if scene == "all":
            fused = [
                {**row, "scene": "chat", "kind": "chat", "lane_score": row["score"], "score": 0.016}
                for row in rows
            ]
            return {"request_id": "s", "scene": "all", "memories": fused, "lanes": {}, "errors": {}}
        if scene in {"working", "coding"}:
            return {
                "request_id": "s",
                "scene": "productivity",
                "variant": scene,
                "facts": [
                    {"fact_id": "f-1", "statement": "use uv", "score": 0.91, "user_id": user_id}
                ],
                "experiences": [],
            }
        return {
            "request_id": "s",
            "scene": "normal",
            "memories": {"normal": rows, "schema": [], "proactive": []},
        }

    def get(self, memory_id, kind=None, variant=None):
        self._record("get", {"memory_id": memory_id, "kind": kind, "variant": variant})
        if kind == "fact":
            return {
                "kind": "fact",
                "variant": variant,
                "fact_id": memory_id,
                "statement": "use uv",
                "status": "active",
                "user_id": "local",
            }
        for user_id, rows in CHAT_ROWS.items():
            for mid, text, _score, layer, source in rows:
                if mid == memory_id:
                    return {**_chat(user_id, mid, text, layer, source), "kind": "chat"}
        return None

    def list_memories(self, **kwargs):
        self._record("list_memories", kwargs)
        user_id = kwargs["user_id"]
        layers = kwargs.get("layers")
        if layers == ["l1_raw"]:
            statuses = set(kwargs.get("statuses") or ["active"])
            rows = [
                {**row, "layer": "l1_raw", "user_id": user_id, "agent_id": "all-apps"}
                for row in RAW_ROWS.get(user_id, [])
                if row["status"] in statuses
                and (
                    not kwargs.get("include_sources") or row["source"] in kwargs["include_sources"]
                )
                and (not kwargs.get("session_ids") or row["session_id"] in kwargs["session_ids"])
            ]
            total = len(rows)
            offset = kwargs.get("offset", 0)
            limit = kwargs.get("limit", 100)
            return {
                "vdb": {
                    "memories": rows[offset : offset + limit],
                    "total": total,
                }
            }
        rows = [_chat(user_id, *row[:2], *row[3:]) for row in CHAT_ROWS.get(user_id, [])]
        if layers:
            rows = [row for row in rows if row["layer"] in layers]
            if "l0_basic_info" in layers:
                rows.append(_chat(user_id, "l0-1", "name is Fei", "l0_basic_info", "hermes"))
        if kwargs.get("scene") == "all":
            return {
                "scene": "all",
                "memories": [{**row, "scene": "chat", "kind": "chat"} for row in rows],
                "total": len(rows),
                "totals": {"chat": len(rows), "working": 0, "coding": 0},
                "errors": {},
            }
        return {"vdb": {"memories": rows, "total": len(rows)}}

    def memory_stats(self, *, user_id, statuses=None):
        return {"total": 0, "request_id": "stats", "elapsed_ms": 1.0}

    def get_user_basic_info(self, *, user_id):
        self._record("get_user_basic_info", {"user_id": user_id})
        return {
            "request_id": f"basic-{user_id}",
            "elapsed_ms": 1.5,
            "memories": [
                {
                    "memory_id": f"{user_id}-l0-head",
                    "content": "The user's name is Fei.",
                    "layer": "l0_basic_info",
                    "status": "active",
                    "memory_at": 1_790_000_100,
                    "gmt_created": 1_790_000_100,
                    "tags": [],
                    "basic_info_kv": {"name": "Fei"},
                    "evolution_chain": [
                        {
                            "node_id": f"{user_id}-l0-head",
                            "content": "The user's name is Fei.",
                            "memory_at": "2026-10-31 10:00",
                            "gmt_created": 1_790_000_100,
                            "layer": "l0_basic_info",
                        },
                        {
                            "node_id": f"{user_id}-l0-old",
                            "content": "The user's name is 费天翔.",
                            "memory_at": "2026-10-30 10:00",
                            "gmt_created": 1_790_000_000,
                            "layer": "l0_basic_info",
                        },
                    ],
                }
            ],
        }


class Handle:
    def __init__(self, sdk: RecordingSdk) -> None:
        self.sdk = sdk

    async def client(self) -> RecordingSdk:
        return self.sdk

    async def close(self) -> None:
        return None


def application(tmp_path: Path, *, home: Path | None = None):
    settings = Settings(
        data_dir=tmp_path / "data",
        source_home=home or tmp_path,
        api_token=SecretStr(TOKEN),
        imports_enabled=home is not None,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )
    return create_app(
        settings,
        secret_store=InMemorySecretStore(),
        source_catalog=(
            build_source_catalog(home=home, environment={}, platform="darwin")
            if home is not None
            else None
        ),
    )


def client_for(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


async def _model_ready(_app) -> bool:
    return True


async def _model_not_ready(_app) -> bool:
    return False


def run_with_sdk(tmp_path, monkeypatch, scenario) -> None:
    async def main() -> None:
        app = application(tmp_path)
        sdk = RecordingSdk()

        @asynccontextmanager
        async def write_client():
            yield sdk

        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle(sdk)
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            monkeypatch.setattr("one_memory.api._recall_model_ready", _model_ready)
            async with client_for(app) as client:
                await scenario(app, sdk, client)

    asyncio.run(main())


PASS_THROUGH = {
    "min_score": 0.5,
    "schema_min_score": 0.6,
    "working_fact_min_score": 0.3,
    "working_experience_min_score": 0.45,
    "schema_limit": 4,
    "intention_limit": 2,
    "rerank": False,
    "reader": "legacy",
    "scene": "chat",
    "search_mode": "agentic",
    "include_sources": ["hermes", "codex"],
    "exclude_sources": ["cursor"],
    "created_after": 1_780_000_000,
    "session_ids": ["s-1"],
    "agent_ids": ["all-apps"],
    "workspace_id": "ws-1",
    "branch": "main",
    "layers": ["l2_fact", "l0_basic_info"],
}
#: How each field reaches `client.search` (lists stay lists).
EXPECTED_SEARCH = {**PASS_THROUGH}


def _memory_search(sdk: RecordingSdk) -> dict:
    return [call for call in sdk.all("search") if call.get("layers") != ["l1_raw"]][-1]


@pytest.mark.fresh_sdk
def test_every_context_search_option_reaches_the_sdk_and_absent_ones_keep_the_old_call(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        baseline = await client.post(
            "/api/v2/context/search", json={"query": "tea", "kinds": ["memory"]}
        )
        assert baseline.status_code == 200, baseline.text
        before = _memory_search(sdk)
        assert before["min_score"] == before["schema_min_score"] == 0.35
        assert before["scene"] == "normal" and before["reader"] == "hybrid"
        assert before["schema_limit"] == 3 and before["intention_limit"] == 1
        for absent in ("rerank", "search_mode", "layers", "session_ids", "created_after"):
            assert absent not in before

        response = await client.post(
            "/api/v2/context/search",
            json={"query": "tea", "kinds": ["memory"], **PASS_THROUGH},
        )
        assert response.status_code == 200, response.text
        sent = _memory_search(sdk)
        for name, value in EXPECTED_SEARCH.items():
            assert sent[name] == value, name
        assert sent["user_ids"] == ["local"]

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_raw_context_search_can_return_turns_instead_of_storage_batches(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        response = await client.post(
            "/api/v2/context/search",
            json={
                "query": "绿茶",
                "kinds": ["raw"],
                "raw_unit": "turn",
                "limit": 20,
            },
        )
        assert response.status_code == 200, response.text
        raw_one = [item for item in response.json()["items"] if item["raw_id"] == "raw-1"]
        assert len(raw_one) == 1
        assert raw_one[0]["turn_index"] == 1
        assert "我喜欢绿茶" in raw_one[0]["content"]
        assert "我叫费天翔" not in raw_one[0]["content"]
        # pro/ultra: a shadow raw is already in its extracted memories.
        assert "statuses" not in sdk.last("search")

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_lite_raw_search_is_plain_rag_over_active_and_shadow_raws(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        app.state.memory_mode = lambda: "lite"
        response = await client.post(
            "/api/v2/context/search",
            json={"query": "喜欢喝绿茶的人", "kinds": ["raw"], "raw_unit": "turn", "limit": 5},
        )
        assert response.status_code == 200, response.text
        sent = sdk.last("search")
        assert sent["statuses"] == ["active", "shadow"] and sent["limit"] == 5
        items = response.json()["items"]
        # No turn contains the query, yet every vector hit is returned, in
        # score order: raw-1 by its closest turn, raw-2 by its only one.
        assert [(item["raw_id"], item["turn_index"]) for item in items] == [
            ("raw-1", 1),
            ("raw-2", 0),
        ]
        assert [item["score"] for item in items] == [0.97, 0.3]
        assert "我喜欢绿茶" in items[0]["content"]

        floored = await client.post(
            "/api/v2/context/search",
            json={
                "query": "喜欢喝绿茶的人",
                "kinds": ["raw"],
                "raw_unit": "turn",
                "min_score": 0.5,
            },
        )
        assert [item["raw_id"] for item in floored.json()["items"]] == ["raw-1"]

        # Lite has no model, so recall answers from the raw layer.
        monkeypatch.setattr("one_memory.api._recall_model_ready", _model_not_ready)
        recall = await client.post("/api/v1/recall", json={"query": "喜欢喝绿茶的人"})
        assert recall.status_code == 200, recall.text
        assert recall.json()["mode"] == "raw"
        assert sdk.last("search")["statuses"] == ["active", "shadow"]
        assert [hit["memory_id"] for hit in recall.json()["hits"]][:1] == ["raw-1"]

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_long_semantic_raw_hit_cannot_erase_an_earlier_match(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        app.state.memory_mode = lambda: "lite"

        def search(query, **kwargs):
            sdk._record("search", {"query": query, **kwargs})
            memories = [
                {
                    "memory_id": "raw-target",
                    "content": "[user]: lite 模式的 3d 图需要调整",
                    "layer": "l1_raw",
                    "status": "active",
                    "user_id": "local",
                    "agent_id": "all-apps",
                    "session_id": "target-session",
                    "source": "cursor",
                    "memory_at": 1_790_100_000,
                    "score": 0.97,
                },
                {
                    "memory_id": "raw-long",
                    "content": f"[user]: {'x' * 20_001}",
                    "layer": "l1_raw",
                    "status": "active",
                    "user_id": "local",
                    "agent_id": "all-apps",
                    "session_id": "long-session",
                    "source": "cursor",
                    "memory_at": 1_790_000_000,
                    "score": 0.9,
                },
            ]
            return {
                "request_id": "long-raw",
                "memories": memories[1:] if query == "oversized" else memories,
            }

        sdk.search = search
        response = await client.post(
            "/api/v2/context/search",
            json={
                "query": "3d",
                "kinds": ["raw"],
                "raw_unit": "turn",
                "limit": 20,
                "character_budget": 20_000,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "grounded"
        assert body["items"][0]["raw_id"] == "raw-target"
        assert "3d" in body["items"][0]["content"]
        assert body["items"][1]["raw_id"] == "raw-long"
        assert body["items"][1]["truncated"] is True
        assert body["truncated"] is True

        single = await client.post(
            "/api/v2/context/search",
            json={
                "query": "oversized",
                "kinds": ["raw"],
                "raw_unit": "turn",
                "character_budget": 20_000,
            },
        )
        assert single.json()["items"][0]["truncated"] is True
        assert single.json()["truncated"] is True

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_raw_only_lane_failure_is_unavailable_not_an_empty_partial(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        app.state.memory_mode = lambda: "lite"

        def search(_query, **_kwargs):
            raise ValueError("private conversation text must not reach logs")

        sdk.search = search
        response = await client.post(
            "/api/v2/context/search",
            json={"query": "3d", "kinds": ["raw"], "raw_unit": "turn"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "unavailable"
        assert response.json()["items"] == []

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_every_recall_and_ask_option_reaches_the_sdk(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        recalled = await client.post(
            "/api/v1/recall", json={"query": "tea", "user_id": "wife", **PASS_THROUGH}
        )
        assert recalled.status_code == 200, recalled.text
        sent = _memory_search(sdk)
        for name, value in EXPECTED_SEARCH.items():
            assert sent[name] == value, name
        assert sent["user_ids"] == ["wife"]

        ask_options = {
            name: value
            for name, value in PASS_THROUGH.items()
            if name
            not in {
                "working_fact_min_score",
                "working_experience_min_score",
                "schema_limit",
                "intention_limit",
                "reader",
                "scene",
                "search_mode",
                "workspace_id",
                "branch",
            }
        }
        asked = await client.post(
            "/api/v2/memories/ask", json={"question": "tea?", "user_id": "wife", **ask_options}
        )
        assert asked.status_code == 200, asked.text
        sent = _memory_search(sdk)
        for name, value in ask_options.items():
            assert sent[name] == value, name
        assert sent["user_ids"] == ["wife"] and sent["scene"] == "normal"

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_min_score_is_enforced_on_what_core_returns(tmp_path, monkeypatch) -> None:
    """Acceptance item 9: `min_score=0.9` answers only results scoring ≥ 0.9."""

    async def scenario(app, sdk, client):
        context = await client.post(
            "/api/v2/context/search", json={"query": "tea", "min_score": 0.9}
        )
        assert context.status_code == 200, context.text
        items = context.json()["items"]
        assert items and all(item["score"] >= 0.9 for item in items)
        assert {item.get("memory_id") or item.get("raw_id") for item in items} == {
            "m-high",
            "raw-1",
        }
        raw_call = sdk.last("search") if sdk.last("search").get("layers") else None
        assert raw_call is None or raw_call["min_score"] == 0.9

        # The fused scene ranks by 1/(60+rank); the lane's own score is the floor's.
        fused = await client.post(
            "/api/v2/context/search",
            json={"query": "tea", "kinds": ["memory"], "scene": "all", "min_score": 0.9},
        )
        assert [item["memory_id"] for item in fused.json()["items"]] == ["m-high"]
        assert fused.json()["items"][0]["score"] == 0.95

        recalled = await client.post("/api/v1/recall", json={"query": "tea", "min_score": 0.9})
        assert recalled.status_code == 200, recalled.text
        assert [hit["memory_id"] for hit in recalled.json()["hits"]] == ["m-high"]

        # A working-scene fact is read back from its own namespace.
        working = await client.post(
            "/api/v2/context/search",
            json={"query": "uv", "kinds": ["memory"], "scene": "working", "min_score": 0.9},
        )
        assert [item["memory_id"] for item in working.json()["items"]] == ["f-1"]
        assert sdk.last("get") == {"memory_id": "f-1", "kind": "fact", "variant": "working"}

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
@pytest.mark.parametrize(
    "extra",
    [
        {"min_score": 1.5},
        {"min_score": -0.1},
        {"schema_min_score": "high"},
        {"scene": "normal"},
        {"search_mode": "fast"},
        {"reader": "vector"},
        {"layers": ["l9_nothing"]},
        {"layers": []},
        {"branch": "main"},
        {"rerank": "no"},
        {"created_after": -1},
        {"user_ids": ["wife"]},
        {"embed_override": {"model": "x"}},
        {"query_embedding": [0.1]},
        {"request_id": "r-1"},
    ],
)
def test_out_of_range_and_unexposed_search_fields_answer_422(tmp_path, extra) -> None:
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            for path, body in (
                ("/api/v2/context/search", {"query": "q"}),
                ("/api/v1/recall", {"query": "q"}),
                ("/api/v2/memories/ask", {"question": "q"}),
            ):
                response = await client.post(path, json={**body, **extra})
                assert response.status_code == 422, (path, response.text)

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_raw_lane_takes_the_callers_sources_only_inside_its_authority(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        response = await client.post(
            "/api/v2/context/search",
            json={
                "query": "tea",
                "kinds": ["raw"],
                "include_sources": ["hermes"],
                "session_ids": ["s-1"],
                "created_after": 1_700_000_000,
            },
        )
        assert response.status_code == 200, response.text
        raw_call = sdk.last("search")
        assert raw_call["layers"] == ["l1_raw"]
        assert raw_call["include_sources"] == ["hermes"]
        assert raw_call["session_ids"] == ["s-1"]
        assert raw_call["created_after"] == 1_700_000_000
        # Memory-lane knobs never reach the raw search.
        assert raw_call["scene"] == "chat" and raw_call["schema_limit"] == 0

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_library_honours_layers_statuses_and_filters(tmp_path, monkeypatch) -> None:
    """Acceptance item 10: `layer=l0_basic_info` returns only L0 records."""

    async def scenario(app, sdk, client):
        page = await client.get("/api/v2/memory-library", params={"layer": "l0_basic_info"})
        assert page.status_code == 200, page.text
        assert {memory["layer"] for memory in page.json()["memories"]} == {"l0_basic_info"}
        sent = sdk.last("list_memories")
        assert sent["layers"] == ["l0_basic_info"] and sent["scene"] is None

        await client.get(
            "/api/v2/memory-library",
            params=[
                ("layers", "l2_fact,l6_schema"),
                ("layers", "l7_intention"),
                ("statuses", "active,superseded"),
                ("session_ids", "s-1"),
                ("agent_ids", "all-apps"),
                ("exclude_sources", "cursor"),
                ("user_id", "wife"),
            ],
        )
        sent = sdk.last("list_memories")
        assert sent["layers"] == ["l2_fact", "l6_schema", "l7_intention"]
        assert sent["statuses"] == ["active", "superseded"]
        assert sent["session_ids"] == ["s-1"] and sent["agent_ids"] == ["all-apps"]
        assert sent["exclude_sources"] == ["cursor"] and sent["user_id"] == "wife"

        # Without a layer the page is what it always was.
        await client.get("/api/v2/memory-library")
        sent = sdk.last("list_memories")
        assert sent["scene"] == "all" and "layers" not in sent

        searched = await client.get(
            "/api/v2/memory-library", params={"query": "tea", "layers": "l2_fact"}
        )
        assert searched.status_code == 200, searched.text
        assert sdk.last("search")["layers"] == ["l2_fact"]

        for params in (
            {"layers": "l9_nothing"},
            {"statuses": "gone"},
            {"layers": "l2_fact", "scene": "working"},
        ):
            refused = await client.get("/api/v2/memory-library", params=params)
            assert refused.status_code == 422, (params, refused.text)

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_layer_counts_read_each_layer_for_one_user(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        counts = await client.get("/api/v2/memory-library/layers", params={"user_id": "wife"})
        assert counts.status_code == 200, counts.text
        body = counts.json()
        assert body["layers"]["l1_raw"] == 1
        assert body["layers"]["l2_fact"] == 1
        assert body["layers"]["l0_basic_info"] == 1
        assert body["total"] == sum(body["layers"].values())
        calls = sdk.all("list_memories")
        assert {call["user_id"] for call in calls} == {"wife"}
        [raw_call] = [call for call in calls if call["layers"] == ["l1_raw"]]
        assert raw_call["statuses"] == ["active", "shadow"]
        assert (
            await client.get("/api/v2/memory-library/layers", params={"layer": "x"})
        ).status_code == 422

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_raw_lists_active_and_shadow_turns_with_roles_and_hashes(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        page = await client.get("/api/v2/raw")
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["total"] == 2 and len(body["items"]) == 2
        sent = sdk.last("list_memories")
        assert sent["layers"] == ["l1_raw"] and sent["statuses"] == ["active", "shadow"]
        first = next(item for item in body["items"] if item["raw_id"] == "raw-1")
        assert first["status"] == "shadow"
        assert [message["role"] for message in first["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert first["messages"][0]["content"] == "我叫费天翔"
        assert first["role"] is None
        expected = hashlib.sha256(first["content"].encode()).hexdigest()
        assert first["content_hash"] == f"sha256:{expected}"
        assert first["session_id"] == "s-1" and first["source"] == "hermes"
        assert first["created"].startswith("2026-")
        second = next(item for item in body["items"] if item["raw_id"] == "raw-2")
        assert second["role"] == "user"

        turns = (await client.get("/api/v2/raw", params={"unit": "turn"})).json()
        assert turns["unit"] == "turn" and turns["total"] == 3
        first_turns = [item for item in turns["items"] if item["raw_id"] == "raw-1"]
        assert [item["turn_index"] for item in first_turns] == [1, 0]
        assert len({item["turn_id"] for item in first_turns}) == 2
        assert [[message["content"] for message in item["messages"]] for item in first_turns] == [
            ["我喜欢绿茶", "也记住了。"],
            ["我叫费天翔", "好的，记住了。"],
        ]
        cursor = None
        first_cursor = None
        cursor_turns = []
        cursor_calls = len(sdk.all("list_memories"))
        while True:
            params = {"unit": "turn", "paging": "cursor", "limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            response = await client.get("/api/v2/raw", params=params)
            assert response.status_code == 200, response.text
            cursor_page = response.json()
            assert cursor_page["total_exact"] is False
            assert len(cursor_page["items"]) == 1
            cursor_turns.append(cursor_page["items"][0]["messages"][0]["content"])
            cursor = cursor_page["next_cursor"]
            if first_cursor is None:
                first_cursor = cursor
            if cursor is None:
                assert cursor_page["has_more"] is False
                break
            assert cursor_page["has_more"] is True
        assert cursor_turns == ["我喜欢绿茶", "我叫费天翔", "你好"]
        assert len(sdk.all("list_memories")) == cursor_calls + 3
        graph_calls = len(sdk.all("list_memories"))
        graph = await client.get("/api/v2/raw/graph", params={"limit": 2})
        assert graph.status_code == 200, graph.text
        assert len(sdk.all("list_memories")) == graph_calls + 1
        graph_body = graph.json()
        assert graph_body["total"] == 3
        assert graph_body["has_more"] is True
        assert [item["label"] for item in graph_body["items"]] == [
            "[user]: 我喜欢绿茶 [assistant]: 也记住了。",
            "[user]: 我叫费天翔 [assistant]: 好的，记住了。",
        ]
        assert (await client.get("/api/v2/raw/graph", params={"limit": 301})).status_code == 422
        assert (
            await client.get(
                "/api/v2/raw/graph",
                params={"limit": 2, "offset": 2},
            )
        ).status_code == 422
        inserted = {
            "memory_id": "raw-new",
            "content": "[user]: 新消息",
            "status": "active",
            "session_id": "s-new",
            "source": "cursor",
            "memory_at": 1_790_200_000,
            "gmt_created": 1_790_200_100,
        }
        RAW_ROWS["local"].insert(0, inserted)
        try:
            updated_graph = (await client.get("/api/v2/raw/graph")).json()
            assert updated_graph["total"] == 4
            assert updated_graph["items"][0]["label"] == "[user]: 新消息"
            continued = await client.get(
                "/api/v2/raw",
                params={
                    "unit": "turn",
                    "paging": "cursor",
                    "limit": 1,
                    "cursor": first_cursor,
                },
            )
            assert continued.status_code == 200, continued.text
            assert continued.json()["items"][0]["messages"][0]["content"] == "我叫费天翔"
        finally:
            RAW_ROWS["local"].remove(inserted)
        assert (
            await client.get(
                "/api/v2/raw",
                params={
                    "unit": "turn",
                    "paging": "cursor",
                    "cursor": first_cursor,
                    "source": "hermes",
                },
            )
        ).status_code == 422
        assert (
            await client.get(
                "/api/v2/raw",
                params={
                    "unit": "turn",
                    "paging": "cursor",
                    "cursor": "turn:²:0:0:2::0000000000000000",
                },
            )
        ).status_code == 422

        calls_before_refresh = len(sdk.all("list_memories"))
        await client.get("/api/v2/raw", params={"unit": "turn"})
        assert len(sdk.all("list_memories")) == calls_before_refresh + 1

        # A live session puts a new raw on top: one listing call, not a rescan.
        newest = {
            **RAW_ROWS["local"][1],
            "memory_id": "raw-new",
            "content": "[user]: 刚刚说的",
            "status": "active",
        }
        original = RAW_ROWS["local"]
        monkeypatch.setitem(RAW_ROWS, "local", [newest, *original])
        calls_before_append = len(sdk.all("list_memories"))
        appended = (await client.get("/api/v2/raw", params={"unit": "turn"})).json()
        assert len(sdk.all("list_memories")) == calls_before_append + 1
        assert appended["total"] == 4
        assert [item["raw_id"] for item in appended["items"]][:1] == ["raw-new"]
        assert [item["turn_id"] for item in appended["items"][1:]] == [
            item["turn_id"] for item in turns["items"]
        ]
        # Anything else (here a raw going away) lists the raw layer again.
        monkeypatch.setitem(RAW_ROWS, "local", [newest, original[1]])
        calls_before_delete = len(sdk.all("list_memories"))
        shrunk = (await client.get("/api/v2/raw", params={"unit": "turn"})).json()
        assert len(sdk.all("list_memories")) > calls_before_delete + 1
        assert shrunk["total"] == 2
        monkeypatch.setitem(RAW_ROWS, "local", original)
        calls_before_restore = len(sdk.all("list_memories"))
        restored = (await client.get("/api/v2/raw", params={"unit": "turn"})).json()
        assert len(sdk.all("list_memories")) > calls_before_restore + 1
        assert restored["total"] == 3

        ascending = (
            await client.get("/api/v2/raw", params={"unit": "turn", "order": "asc"})
        ).json()
        ascending_first = [item for item in ascending["items"] if item["raw_id"] == "raw-1"]
        assert [item["turn_index"] for item in ascending_first] == [0, 1]

        await client.get(
            "/api/v2/raw",
            params=[("source", "hermes,codex"), ("session_id", "s-1"), ("order", "asc")],
        )
        sent = sdk.last("list_memories")
        assert sent["include_sources"] == ["hermes", "codex"]
        assert sent["session_ids"] == ["s-1"] and sent["order"] == "asc"

        windowed = (await client.get("/api/v2/raw", params={"after": 1_790_050_000})).json()
        assert windowed["time_filtered"] is True
        assert [item["raw_id"] for item in windowed["items"]] == ["raw-2"]

        wife = (await client.get("/api/v2/raw", params={"user_id": "wife"})).json()
        assert [item["raw_id"] for item in wife["items"]] == ["raw-w"]
        assert sdk.last("list_memories")["user_id"] == "wife"
        assert (await client.get("/api/v2/raw", params={"statuses": "bogus"})).status_code == 422

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_profile_versions_read_the_l0_chain_for_one_user(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        response = await client.get("/api/v2/user-profile/versions", params={"user_id": "wife"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_id"] == "wife"
        assert sdk.last("get_user_basic_info") == {"user_id": "wife"}
        [chain] = body["chains"]
        assert chain["memory_id"] == "wife-l0-head"
        assert chain["basic_info"] == {"name": "Fei"}
        assert [(v["memory_id"], v["current"]) for v in chain["versions"]] == [
            ("wife-l0-head", True),
            ("wife-l0-old", False),
        ]
        local = (await client.get("/api/v2/user-profile/versions")).json()
        assert local["user_id"] == "local"
        assert sdk.last("get_user_basic_info") == {"user_id": "local"}
        refused = await client.get("/api/v2/user-profile/versions", params={"owner": "wife"})
        assert refused.status_code == 422

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_graph_takes_layers_and_session_filters(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        response = await client.get(
            "/api/v2/memories/graph",
            params={"layers": "l2_fact", "session_ids": "s-1", "user_id": "wife"},
        )
        assert response.status_code == 200, response.text
        sent = sdk.last("list_memories")
        assert sent["layers"] == ["l2_fact"] and sent["scene"] is None
        assert sent["session_ids"] == ["s-1"] and sent["user_id"] == "wife"

    run_with_sdk(tmp_path, monkeypatch, scenario)


def _hermes_state_db(root: Path, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "state.db")
    connection.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
        " content TEXT, timestamp REAL, tool_name TEXT, active INTEGER)"
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, 1790000000, NULL, 1)",
        [(1, "session-1", "user", text), (2, "session-1", "assistant", "ok")],
    )
    connection.commit()
    connection.close()


@pytest.mark.fresh_sdk
def test_v2_source_and_import_aliases_behave_like_v1_per_user(tmp_path) -> None:
    async def scenario():
        home = tmp_path / "home"
        hermes = home / ".hermes"
        wife = hermes / "profiles" / "wife"
        _hermes_state_db(hermes, "I am the husband")
        _hermes_state_db(wife, "I am the wife")
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            location = (await client.post("/api/v1/sources/hermes/location")).json()
            local = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "source_roots": {"hermes": location["root_id"]},
                    "enable_sync": True,
                },
            )
            assert local.status_code == 200, local.text
            connected = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "user_id": "wife",
                    "root": str(wife),
                },
            )
            assert connected.status_code == 200, connected.text
            for job in (*local.json()["jobs"], *connected.json()["jobs"]):
                await app.state.import_supervisor.wait(job["job_id"])

            v1 = (await client.get("/api/v1/sources")).json()
            v2 = (await client.get("/api/v2/sources")).json()
            assert v1 == v2
            wife_sources = (await client.get("/api/v2/sources", params={"user_id": "wife"})).json()
            assert [source["source_id"] for source in wife_sources] == ["hermes"]
            assert wife_sources[0]["authorized"] is True
            assert (await client.get("/api/v2/sources", params={"u": "x"})).status_code == 422

            created = await client.post(
                "/api/v2/import-jobs",
                json={"source_id": "hermes", "operation": "sync", "user_id": "wife"},
            )
            assert created.status_code == 200, created.text
            wife_job = created.json()
            assert wife_job["user_id"] == "wife" and wife_job["source_id"] == "hermes"
            await app.state.import_supervisor.wait(wife_job["job_id"])
            refused = await client.post(
                "/api/v2/import-jobs",
                json={"source_id": "claude-code", "user_id": "wife"},
            )
            assert refused.status_code in {404, 422}
            unknown = await client.post(
                "/api/v2/import-jobs",
                json={"source_id": "hermes", "from_beginning": True},
            )
            assert unknown.status_code == 422

            listed = (await client.get("/api/v2/import-jobs", params={"user_id": "wife"})).json()
            assert {job["user_id"] for job in listed} == {"wife"}
            assert (await client.get("/api/v2/import-jobs")).json() == (
                await client.get("/api/v1/import-jobs")
            ).json()
            path = f"/api/v2/import-jobs/{wife_job['job_id']}"
            assert (await client.get(path)).status_code == 404
            own = await client.get(path, params={"user_id": "wife"})
            assert own.status_code == 200 and own.json()["job_id"] == wife_job["job_id"]

            synced = await client.post("/api/v2/sources/hermes/sync", json={"user_id": "wife"})
            v1_synced = await client.post("/api/v1/sources/hermes/sync")
            assert synced.status_code == v1_synced.status_code, (synced.text, v1_synced.text)
            assert synced.status_code == 200
            assert synced.json()["user_id"] == "wife"
            assert v1_synced.json()["user_id"] == "local"
            other = await client.post("/api/v2/sources/claude-code/sync", json={"user_id": "wife"})
            assert other.status_code in {404, 422}
            assert (
                await client.post("/api/v2/sources/hermes/sync", json={"owner": "wife"})
            ).status_code == 422

    asyncio.run(scenario())
