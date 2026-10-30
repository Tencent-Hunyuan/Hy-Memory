"""The v2 memory library across chat, working and coding memories.

Keyword search pages are cut from one cached SDK result window (window_id),
the empty-query list mixes the three scenes newest first, and detail, edit
and delete pass the memory's kind/variant through to the SDK.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from hy_memory import AmbiguousMemoryIdError
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.library_search_window import LibrarySearchWindows
from one_memory.secrets import InMemorySecretStore


def application(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("library-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer library-test"},
    )


def chat_row(memory_id, content, *, source="cursor"):
    return {
        "memory_id": memory_id,
        "content": content,
        "layer": "l2_fact",
        "status": "active",
        "user_id": "local",
        "agent_id": "all-apps",
        "session_id": "s1",
        "source": source,
        "tags": [],
        "gmt_created": 1_790_000_000,
    }


EXPERIENCE_MEMORY_AT = 1_789_990_000


def experience_row(memory_id, task, solution, *, variant="working", source="codebuddy"):
    return {
        "experience_id": memory_id,
        "user_id": "local",
        "agent_id": "codebuddy",
        "task": task,
        "solution": solution,
        "source": source,
        # Conversation time, epoch seconds like chat (hy-memory 2.0.7).
        "memory_at": EXPERIENCE_MEMORY_AT,
        "created_at": "2026-10-01T08:00:00",
        "updated_at": "2026-10-02T08:00:00",
        "_variant": variant,
    }


def fact_row(memory_id, statement, *, variant="coding", source="codebuddy"):
    return {
        "fact_id": memory_id,
        "user_id": "local",
        "agent_id": "codebuddy",
        "statement": statement,
        "status": "active",
        "source": source,
        "created_at": "2026-10-03T08:00:00",
        "_variant": variant,
    }


class FakeSdk:
    """Public-API shaped client holding chat, experience and fact memories."""

    def __init__(self):
        self.chat = {}
        self.productivity = {}
        self.search_calls = []
        self.list_calls = []
        self.stats_calls = []
        self.get_calls = []
        self.update_calls = []
        self.delete_calls = []
        self.reverse_next_search = False

    def add_chat(self, row):
        self.chat[row["memory_id"]] = row

    def add_productivity(self, kind, row):
        memory_id = row["experience_id"] if kind == "experience" else row["fact_id"]
        self.productivity[(kind, row["_variant"], memory_id)] = row

    def _unified(self, kind, variant, row):
        if kind == "experience":
            content = f"{row['task']}\n{row['solution']}"
            memory_id = row["experience_id"]
        else:
            content = row["statement"]
            memory_id = row["fact_id"]
        return {
            **{key: value for key, value in row.items() if not key.startswith("_")},
            "scene": variant,
            "kind": kind,
            "memory_id": memory_id,
            "content": content,
            "created_at": 1_790_000_000,
            "updated_at": 1_790_000_100,
        }

    def _matches(self, memory_id, kind, variant):
        found = []
        if kind in (None, "chat") and variant is None and memory_id in self.chat:
            found.append(("chat", None, self.chat[memory_id]))
        for (row_kind, row_variant, row_id), row in self.productivity.items():
            if row_id != memory_id:
                continue
            if kind not in (None, row_kind) or variant not in (None, row_variant):
                continue
            found.append((row_kind, row_variant, row))
        if len(found) > 1:
            raise AmbiguousMemoryIdError(
                memory_id,
                [f"{v}:{k}" if v else k for k, v, _ in found],
            )
        return found[0] if found else None

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        include = kwargs.get("include_sources") or []
        items = [
            {**row, "scene": "chat", "kind": "chat"}
            for row in self.chat.values()
            if not include or row["source"] in include
        ]
        items += [
            self._unified(kind, variant, row)
            for (kind, variant, _id), row in self.productivity.items()
            if not include or row["source"] in include
        ]
        if self.reverse_next_search:
            items.reverse()
        items = items[: kwargs["limit"]]
        for rank, item in enumerate(items, start=1):
            item["score"] = round(1 / (60 + rank), 6)
        return {
            "request_id": "search",
            "scene": "all",
            "memories": items,
            "lanes": {"chat": 0, "working": 0, "coding": 0},
            "errors": {},
            "elapsed_ms": 1.0,
        }

    def list_memories(self, **kwargs):
        self.list_calls.append(kwargs)
        include = kwargs.get("include_sources") or []
        scene = kwargs["scene"]
        start, stop = kwargs["offset"], kwargs["offset"] + kwargs["limit"]
        if scene == "chat":
            rows = [row for row in self.chat.values() if not include or row["source"] in include]
            return {
                "vdb": {
                    "memories": rows[start:stop],
                    "total": len(rows),
                    "limit": kwargs["limit"],
                    "offset": kwargs["offset"],
                },
                "elapsed_ms": 1.0,
            }
        if scene in ("working", "coding"):
            rows = [
                {key: value for key, value in row.items() if not key.startswith("_")}
                | {"status": "active"}
                for (kind, variant, _id), row in self.productivity.items()
                if kind == "experience"
                and variant == scene
                and (not include or row["source"] in include)
            ]
            return {
                "request_id": "list",
                "scene": "productivity",
                "variant": scene,
                "experiences": rows[start:stop],
                "total": len(rows),
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "elapsed_ms": 1.0,
            }
        assert scene == "all"
        chat = [
            {**row, "scene": "chat", "kind": "chat", "created_at": 10, "updated_at": 10}
            for row in self.chat.values()
            if not include or row["source"] in include
        ]
        productivity = [
            {**self._unified(kind, variant, row), "status": "active"}
            for (kind, variant, _id), row in self.productivity.items()
            if kind == "experience" and (not include or row["source"] in include)
        ]
        rows = productivity + chat
        page = rows[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]
        return {
            "scene": "all",
            "memories": page,
            "total": len(rows),
            "totals": {
                "chat": len(chat),
                "working": sum(1 for row in productivity if row["scene"] == "working"),
                "coding": sum(1 for row in productivity if row["scene"] == "coding"),
            },
            "errors": {},
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "request_id": "list",
            "elapsed_ms": 1.0,
        }

    def memory_stats(self, *, user_id, statuses=None):
        self.stats_calls.append({"user_id": user_id, "statuses": statuses})
        experiences = [
            variant for (kind, variant, _id) in self.productivity if kind == "experience"
        ]
        facts = [variant for (kind, variant, _id) in self.productivity if kind == "fact"]
        scenes = {"chat": {"count": len(self.chat)}}
        for variant in ("coding", "working"):
            scenes[variant] = {"count": experiences.count(variant), "facts": facts.count(variant)}
        return {
            "total": len(self.chat) + len(experiences),
            "scenes": scenes,
            "request_id": "stats",
            "elapsed_ms": 1.0,
        }

    def get(self, memory_id, kind=None, variant=None):
        self.get_calls.append((memory_id, kind, variant))
        match = self._matches(memory_id, kind, variant)
        if match is None:
            return None
        row_kind, row_variant, row = match
        if row_kind == "chat":
            return {**row, "kind": "chat", "variant": None}
        payload = {key: value for key, value in row.items() if not key.startswith("_")}
        return {
            "memory_id": memory_id,
            "kind": row_kind,
            "variant": row_variant,
            "scene": "productivity",
            "content": row["solution"] if row_kind == "experience" else row["statement"],
            "layer": row_kind,
            **payload,
        }

    def update(self, memory_id, content, kind=None, variant=None, task=None):
        self.update_calls.append((memory_id, content, kind, variant, task))
        match = self._matches(memory_id, kind, variant)
        if match is None:
            return {"success": False, "error_message": f"memory_id '{memory_id}' not found"}
        row_kind, row_variant, row = match
        if row_kind == "chat":
            row["content"] = content
            return {"success": True, "memory_id": memory_id}
        if row_kind == "experience":
            row["solution"] = content
            if task is not None:
                row["task"] = task
        else:
            row["statement"] = content
        return {"success": True, "memory_id": memory_id, "kind": row_kind, "variant": row_variant}

    def delete(self, memory_id, kind=None, variant=None):
        self.delete_calls.append((memory_id, kind, variant))
        match = self._matches(memory_id, kind, variant)
        if match is None:
            return {"success": False, "deleted_count": 0}
        row_kind, row_variant, row = match
        if row_kind == "chat":
            del self.chat[memory_id]
        else:
            del self.productivity[(row_kind, row_variant, memory_id)]
        return {"success": True, "deleted_count": 1}


def run_with_sdk(tmp_path, monkeypatch, scenario):
    async def main():
        app = application(tmp_path)
        sdk = FakeSdk()

        class Handle:
            async def client(self):
                return sdk

            async def close(self):
                return None

        @asynccontextmanager
        async def write_client():
            yield sdk

        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle()
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            async with client_for(app) as client:
                await scenario(app, sdk, client)

    asyncio.run(main())


@pytest.mark.fresh_sdk
def test_search_pages_come_from_one_cached_window_without_duplicates(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        for index in range(30):
            sdk.add_chat(chat_row(f"chat-{index:02d}", f"deploy note {index}"))
        for index in range(15):
            sdk.add_productivity(
                "experience",
                experience_row(f"exp-{index:02d}", f"deploy task {index}", "run make"),
            )
        sdk.add_chat(chat_row("other", "unrelated words"))

        first = await client.get("/api/v2/memory-library", params={"query": "deploy"})
        assert first.status_code == 200
        body = first.json()
        assert body["total"] == 45
        assert body["limit"] == 20
        assert body["offset"] == 0
        assert body["window_reset"] is False
        assert body["search_window"] is None
        window_id = body["window_id"]
        assert window_id
        assert len(sdk.search_calls) == 1
        query, options = sdk.search_calls[0]
        assert query == "deploy"
        assert options["scene"] == "all"
        assert options["rerank"] is False
        assert options["limit"] == 200
        assert options["min_score"] == 0.0

        # A second search would now rank differently; pages must not care.
        sdk.reverse_next_search = True
        seen = [item["memory_id"] for item in body["memories"]]
        for offset in (20, 40):
            page = await client.get(
                "/api/v2/memory-library",
                params={"query": "deploy", "offset": offset, "window_id": window_id},
            )
            assert page.status_code == 200
            data = page.json()
            assert data["window_id"] == window_id
            assert data["window_reset"] is False
            assert data["offset"] == offset
            assert data["total"] == 45
            seen += [item["memory_id"] for item in data["memories"]]
        assert len(sdk.search_calls) == 1
        assert len(seen) == 45
        assert len(set(seen)) == 45
        scenes = {item["memory_id"]: item for item in body["memories"]}
        assert {item["scene"] for item in scenes.values()} <= {"chat", "working"}

        # Each productivity row is read back in the namespace it was found in.
        productivity_gets = [call for call in sdk.get_calls if call[0].startswith("exp-")]
        assert productivity_gets
        assert all(call[1:] == ("experience", "working") for call in productivity_gets)
        chat_gets = [call for call in sdk.get_calls if call[0].startswith("chat-")]
        assert all(call[1:] == ("chat", None) for call in chat_gets)

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_expired_or_mismatched_window_searches_again_and_restarts(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        now = [1000.0]
        app.state.library_search_windows = LibrarySearchWindows(
            ttl_seconds=60, clock=lambda: now[0]
        )
        for index in range(25):
            sdk.add_chat(chat_row(f"chat-{index:02d}", f"deploy note {index}"))

        first = (await client.get("/api/v2/memory-library", params={"query": "deploy"})).json()
        window_id = first["window_id"]

        now[0] += 61
        expired = await client.get(
            "/api/v2/memory-library",
            params={"query": "deploy", "offset": 20, "window_id": window_id},
        )
        data = expired.json()
        assert data["window_reset"] is True
        assert data["offset"] == 0
        assert data["window_id"] != window_id
        assert len(data["memories"]) == 20
        assert len(sdk.search_calls) == 2

        # A window searched for other words is not reused for this query.
        mismatched = await client.get(
            "/api/v2/memory-library",
            params={"query": "note", "offset": 20, "window_id": data["window_id"]},
        )
        assert mismatched.json()["window_reset"] is True
        assert mismatched.json()["offset"] == 0

        unknown = await client.get(
            "/api/v2/memory-library",
            params={"query": "deploy", "window_id": "never-issued"},
        )
        assert unknown.json()["window_reset"] is True

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_keyword_filter_reads_experience_and_fact_text(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "lunch preferences"))
        sdk.add_productivity(
            "experience",
            experience_row("exp-1", "Fix flaky tests", "Pin the pytest seed"),
        )
        sdk.add_productivity("fact", fact_row("fact-1", "The pytest suite runs in CI"))

        response = await client.get(
            "/api/v2/memory-library", params={"query": "PYTEST", "source": "codebuddy"}
        )
        body = response.json()
        assert body["total"] == 2
        assert sdk.search_calls[0][1]["include_sources"] == ["codebuddy"]
        by_id = {item["memory_id"]: item for item in body["memories"]}
        experience = by_id["exp-1"]
        assert experience["scene"] == "working"
        assert experience["kind"] == "experience"
        assert experience["variant"] == "working"
        assert experience["task"] == "Fix flaky tests"
        assert experience["solution"] == "Pin the pytest seed"
        assert experience["content"] == "Fix flaky tests\nPin the pytest seed"
        assert experience["status"] == "active"
        assert experience["updated_at"] > experience["created_at"]
        assert experience["memory_at"] == EXPERIENCE_MEMORY_AT
        fact = by_id["fact-1"]
        assert (fact["scene"], fact["kind"], fact["variant"]) == ("coding", "fact", "coding")
        assert fact["content"] == "The pytest suite runs in CI"

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_full_search_window_is_reported(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        for index in range(205):
            sdk.add_chat(chat_row(f"chat-{index:03d}", f"note {index}"))
        body = (await client.get("/api/v2/memory-library", params={"query": "note"})).json()
        assert body["search_window"] == 200
        assert body["total"] == 200

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_empty_query_lists_all_scenes_with_source_filter_and_totals(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "cursor chat", source="cursor"))
        sdk.add_chat(chat_row("chat-2", "codebuddy chat", source="codebuddy"))
        sdk.add_productivity("experience", experience_row("exp-w", "task w", "solution w"))
        sdk.add_productivity(
            "experience", experience_row("exp-c", "task c", "solution c", variant="coding")
        )

        response = await client.get("/api/v2/memory-library")
        body = response.json()
        assert response.status_code == 200
        assert body["limit"] == 20
        assert body["total"] == 4
        assert body["totals"] == {"chat": 2, "working": 1, "coding": 1}
        assert body["window_id"] is None
        assert [item["scene"] for item in body["memories"]] == [
            "working",
            "coding",
            "chat",
            "chat",
        ]
        assert [item["memory_at"] for item in body["memories"][:2]] == [
            EXPERIENCE_MEMORY_AT,
            EXPERIENCE_MEMORY_AT,
        ]
        call = sdk.list_calls[0]
        assert call["scene"] == "all"
        assert call["include_sources"] is None
        assert call["statuses"] == ["active"]
        assert (call["offset"], call["limit"]) == (0, 20)

        filtered = await client.get(
            "/api/v2/memory-library", params={"source": "codebuddy", "offset": 1}
        )
        data = filtered.json()
        assert sdk.list_calls[1]["include_sources"] == ["codebuddy"]
        assert sdk.list_calls[1]["offset"] == 1
        assert data["total"] == 3
        assert data["offset"] == 1
        assert [item["memory_id"] for item in data["memories"]] == ["exp-c", "chat-2"]

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_detail_edit_delete_pass_kind_and_variant(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "hello"))
        sdk.add_productivity("experience", experience_row("exp-1", "Old task", "Old solution"))
        sdk.add_productivity("fact", fact_row("fact-1", "Old statement"))

        detail = await client.get(
            "/api/v2/memories/exp-1", params={"kind": "experience", "variant": "working"}
        )
        assert detail.status_code == 200
        record = detail.json()
        assert (record["scene"], record["kind"], record["variant"]) == (
            "working",
            "experience",
            "working",
        )
        assert record["task"] == "Old task"
        assert record["solution"] == "Old solution"
        assert record["content"] == "Old task\nOld solution"
        assert record["memory_at"] == EXPERIENCE_MEMORY_AT
        assert sdk.get_calls[-1] == ("exp-1", "experience", "working")

        # Chat detail with no namespace keeps the plain SDK call.
        chat = await client.get("/api/v2/memories/chat-1")
        assert chat.json()["kind"] == "chat"
        assert chat.json()["scene"] == "chat"
        assert sdk.get_calls[-1] == ("chat-1", None, None)

        edited = await client.put(
            "/api/v2/memories/exp-1",
            json={
                "content": "New solution",
                "task": "New task",
                "kind": "experience",
                "variant": "working",
            },
        )
        assert edited.status_code == 200
        assert sdk.update_calls[-1] == (
            "exp-1",
            "New solution",
            "experience",
            "working",
            "New task",
        )
        assert edited.json()["solution"] == "New solution"
        assert edited.json()["task"] == "New task"

        fact = await client.put(
            "/api/v2/memories/fact-1",
            json={"content": "New statement", "kind": "fact", "variant": "coding"},
        )
        assert fact.status_code == 200
        assert fact.json()["content"] == "New statement"
        refused = await client.put(
            "/api/v2/memories/fact-1",
            json={"content": "x", "task": "y", "kind": "fact"},
        )
        assert refused.status_code == 422

        deleted = await client.delete(
            "/api/v2/memories/fact-1", params={"kind": "fact", "variant": "coding"}
        )
        assert deleted.status_code == 204
        assert sdk.delete_calls[-1] == ("fact-1", "fact", "coding")

        bad = await client.get("/api/v2/memories/exp-1", params={"kind": "nope"})
        assert bad.status_code == 422

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_ambiguous_memory_id_is_a_conflict(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("dup", "chat text"))
        sdk.add_productivity("experience", experience_row("dup", "task", "solution"))

        for method in ("get", "delete"):
            response = await getattr(client, method)("/api/v2/memories/dup")
            assert response.status_code == 409
            assert response.json()["detail"] == "memory_id_ambiguous"
        update = await client.put("/api/v2/memories/dup", json={"content": "x"})
        assert update.status_code == 409
        assert update.json()["detail"] == "memory_id_ambiguous"

        resolved = await client.get("/api/v2/memories/dup", params={"kind": "chat"})
        assert resolved.status_code == 200
        assert resolved.json()["content"] == "chat text"

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_scene_filter_lists_one_scene(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "chat text"))
        sdk.add_productivity("experience", experience_row("work-1", "w task", "w fix"))
        sdk.add_productivity(
            "experience", experience_row("code-1", "c task", "c fix", variant="coding")
        )

        working = await client.get("/api/v2/memory-library", params={"scene": "working"})
        assert working.status_code == 200
        body = working.json()
        assert body["total"] == 1
        assert [item["memory_id"] for item in body["memories"]] == ["work-1"]
        assert body["memories"][0]["kind"] == "experience"
        assert body["memories"][0]["variant"] == "working"
        assert body["memories"][0]["scene"] == "working"
        assert sdk.list_calls[-1]["scene"] == "working"

        chat = await client.get("/api/v2/memory-library", params={"scene": "chat"})
        assert chat.status_code == 200
        assert [item["memory_id"] for item in chat.json()["memories"]] == ["chat-1"]
        assert sdk.list_calls[-1]["scene"] == "chat"

        default = await client.get("/api/v2/memory-library")
        assert default.status_code == 200
        assert sdk.list_calls[-1]["scene"] == "all"

        bad = await client.get("/api/v2/memory-library", params={"scene": "normal"})
        assert bad.status_code == 422

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_scene_filter_narrows_search_and_binds_the_window(tmp_path, monkeypatch):
    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "deploy chat"))
        sdk.add_productivity("experience", experience_row("work-1", "deploy task", "run make"))
        sdk.add_productivity(
            "experience",
            experience_row("code-1", "deploy code", "run tests", variant="coding"),
        )

        coding = await client.get(
            "/api/v2/memory-library", params={"query": "deploy", "scene": "coding"}
        )
        assert coding.status_code == 200
        body = coding.json()
        assert body["total"] == 1
        assert [item["memory_id"] for item in body["memories"]] == ["code-1"]
        assert sdk.search_calls[-1][1]["scene"] == "all"

        # The same window under another scene is a different search.
        other = await client.get(
            "/api/v2/memory-library",
            params={"query": "deploy", "scene": "chat", "window_id": body["window_id"]},
        )
        assert other.status_code == 200
        assert other.json()["window_reset"] is True
        assert [item["memory_id"] for item in other.json()["memories"]] == ["chat-1"]
        assert len(sdk.search_calls) == 2

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_home_summary_counts_what_the_memory_page_lists(tmp_path, monkeypatch):
    """Home showed 55 (chat only) while the memory page showed 94."""

    async def scenario(app, sdk, client):
        sdk.add_chat(chat_row("chat-1", "cursor chat"))
        sdk.add_chat(chat_row("chat-2", "codebuddy chat", source="codebuddy"))
        sdk.add_productivity("experience", experience_row("exp-w", "task w", "solution w"))
        sdk.add_productivity(
            "experience", experience_row("exp-c", "task c", "solution c", variant="coding")
        )
        # Facts are not listed on the memory page, so they are not counted.
        sdk.add_productivity("fact", fact_row("fact-1", "The pytest suite runs in CI"))

        response = await client.get("/api/v2/home-summary")
        assert response.status_code == 200
        body = response.json()
        library = (await client.get("/api/v2/memory-library")).json()
        assert body["memory_count"] == library["total"] == 4
        assert sdk.stats_calls == [{"user_id": "local", "statuses": ["active"]}]
        # The newest memory across scenes, not the newest chat memory.
        home_list = sdk.list_calls[0]
        assert home_list["scene"] == "all"
        assert (home_list["offset"], home_list["limit"], home_list["order"]) == (0, 1, "desc")
        assert body["latest"]["memory_id"] == "exp-w"
        assert body["latest"]["scene"] == "working"
        assert body["latest"]["memory_at"] == EXPERIENCE_MEMORY_AT

    run_with_sdk(tmp_path, monkeypatch, scenario)
