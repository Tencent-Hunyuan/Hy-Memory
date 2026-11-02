"""One Runtime, many users: `user_id` on the memory API, per-user Hermes profiles.

A user is the root that is read (a Hermes profile's own `state.db`), never a
session or platform field. The App never names a user and keeps `local`;
every other harness stays on `local`. These tests drive the production app
against a user-aware stand-in for the public SDK client, whose `get`,
`update` and `delete` take no user exactly like hy-memory 2.0.9's.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import _sdk_recall_target, create_app
from one_memory.config import Settings
from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.adapters import ScannedEvidence, ScanOptions
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
from one_memory.imports.sources.hermes import HERMES_DESCRIPTOR, create_hermes_adapter
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.memory_server.contracts import internal_session_id_for
from one_memory.memory_server.durable_queue import (
    DurableWriteDrain,
    DurableWriteQueue,
    execute_job,
)
from one_memory.sdk_write_processor import SdkWriteProcessor
from one_memory.secrets import InMemorySecretStore
from one_memory.user_scope import lane_key, split_lane, valid_user_id

TOKEN = "multi-user-test"


class UserAwareSdk:
    """The public client surface MemHub calls, holding memories per user."""

    def __init__(self) -> None:
        self.memories: dict[str, dict[str, object]] = {}
        self.add_calls: list[dict[str, object]] = []

    def _store(self, user_id: str, content: str, *, source: str, session_id: str) -> str:
        memory_id = f"memory-{len(self.memories) + 1}"
        self.memories[memory_id] = {
            "memory_id": memory_id,
            "content": content,
            "layer": "l2_fact",
            "status": "active",
            "user_id": user_id,
            "agent_id": "all-apps",
            "session_id": session_id,
            "source": source,
            "tags": [],
            "gmt_created": 1_790_000_000,
        }
        return memory_id

    def add(self, messages, *, user_id, session_id, source, request_id, **_kwargs):
        self.add_calls.append({"user_id": user_id, "request_id": request_id})
        text = " ".join(item["content"] for item in messages if item["role"] == "user")
        memory_id = self._store(user_id, text, source=source, session_id=session_id)
        return {"success": True, "memory_ids": [memory_id]}

    def add_extracted(self, contents, *, user_id, session_id, source, request_id, **_kwargs):
        self.add_calls.append({"user_id": user_id, "request_id": request_id})
        memory_id = self._store(user_id, " ".join(contents), source=source, session_id=session_id)
        return {"success": True, "memory_ids": [memory_id]}

    def _rows(self, user_ids) -> list[dict[str, object]]:
        return [dict(row) for row in self.memories.values() if row["user_id"] in user_ids]

    def list_memories(self, *, user_id, scene=None, offset=0, limit=100, **_kwargs):
        rows = [] if _kwargs.get("layers") == ["l1_raw"] else self._rows({user_id})
        page = rows[offset : offset + limit]
        if scene == "all":
            return {
                "scene": "all",
                "memories": [{**row, "scene": "chat", "kind": "chat"} for row in page],
                "total": len(rows),
                "totals": {"chat": len(rows), "working": 0, "coding": 0},
                "errors": {},
                "limit": limit,
                "offset": offset,
                "request_id": "list",
                "elapsed_ms": 1.0,
            }
        return {"vdb": {"memories": page, "total": len(rows), "limit": limit, "offset": offset}}

    def memory_stats(self, *, user_id, statuses=None):
        return {"total": len(self._rows({user_id})), "request_id": "stats", "elapsed_ms": 1.0}

    def search(self, query, *, user_ids, scene=None, **kwargs):
        rows = [] if kwargs.get("layers") == ["l1_raw"] else self._rows(set(user_ids))
        items = [{**row, "scene": "chat", "kind": "chat", "score": 0.9} for row in rows]
        if scene == "all":
            return {
                "request_id": "search",
                "scene": "all",
                "memories": items,
                "lanes": {"chat": len(items), "working": 0, "coding": 0},
                "errors": {},
                "elapsed_ms": 1.0,
            }
        return {"request_id": "search", "scene": scene or "normal", "memories": items}

    def get(self, memory_id, kind=None, variant=None):
        row = self.memories.get(memory_id)
        return None if row is None else {**row, "kind": "chat", "variant": None}

    def update(self, memory_id, content, kind=None, variant=None, task=None):
        if memory_id not in self.memories:
            return {"success": False, "error_message": f"memory_id '{memory_id}' not found"}
        self.memories[memory_id]["content"] = content
        return {"success": True, "memory_id": memory_id}

    def delete(self, memory_id, kind=None, variant=None):
        if self.memories.pop(memory_id, None) is None:
            return {"success": False, "deleted_count": 0}
        return {"success": True, "deleted_count": 1}

    def get_user_profile(self, *, user_id):
        return {
            "request_id": f"profile-{user_id}",
            "profile": {
                "memory_id": f"l0-{user_id}",
                "content": f"name is {user_id}",
                "layer": "L0_BASIC_INFO",
                "basic_info": {"name": user_id},
            },
            "elapsed_ms": 1.0,
        }


class Handle:
    def __init__(self, sdk: UserAwareSdk) -> None:
        self.sdk = sdk

    async def client(self) -> UserAwareSdk:
        return self.sdk

    async def close(self) -> None:
        return None


class Coordinator:
    """The production write processor over the stand-in client."""

    def __init__(self, sdk: UserAwareSdk) -> None:
        self.processor = SdkWriteProcessor(Handle(sdk))  # type: ignore[arg-type]

    async def process_sdk_job(self, records, *, request_id, before_hy_invocation):
        return await self.processor.process_sdk_job(
            records, request_id=request_id, before_hy_invocation=before_hy_invocation
        )


async def _never(_job_id: str) -> None:
    return None


async def drain_all(queue: DurableWriteQueue, sdk: UserAwareSdk) -> None:
    """Claim and run every pending job, ungated, the way the drain would."""
    drain = DurableWriteDrain(queue, execute=_never, lease=timedelta(seconds=30))
    while claimed := drain.claim(limit=8):
        for job_id in claimed:
            await execute_job(
                job_id,
                queue=queue,
                supervisor=Coordinator(sdk),
                evidence=None,  # type: ignore[arg-type]
            )


def application(tmp_path: Path, *, home: Path | None = None):
    """The production app; with `home`, imports run over that home's sources."""
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


def run_with_sdk(tmp_path, monkeypatch, scenario) -> None:
    async def main() -> None:
        app = application(tmp_path)
        sdk = UserAwareSdk()

        @asynccontextmanager
        async def write_client():
            yield sdk

        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle(sdk)
            monkeypatch.setattr(app.state.sdk_write_coordinator, "use_client", write_client)
            async with client_for(app) as client:
                await scenario(app, sdk, client)

    asyncio.run(main())


def _messages(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}, {"role": "assistant", "content": "noted"}]


async def _write_both_users(app, sdk, client) -> dict[str, str]:
    """`local` and `wife` each add one memory and submit one conversation."""
    receipts = {}
    for user, word in (("local", "tea"), ("wife", "coffee")):
        request_id = str(uuid4())
        body = {"request_id": request_id, "text": f"{user} drinks {word}", "scope": "all-apps"}
        if user != "local":
            body["user_id"] = user
        added = await client.post("/api/v2/memories", json=body)
        assert added.status_code == 201, added.text
        receipts[f"add:{user}"] = request_id
        write = {
            "request_id": "same-request-id",
            "session_id": "same-session",
            "source_id": "one-memory-manual",
            "messages": _messages(f"{user} walks the {word} route"),
        }
        if user != "local":
            write["user_id"] = user
        written = await client.post("/api/v2/memory-writes", json=write)
        assert written.status_code == 202, written.text
        assert written.json()["request_id"] == "same-request-id"
    await drain_all(app.state.memory_write_queue, sdk)
    return receipts


def _contents(page: dict) -> set[str]:
    return {memory["content"] for memory in page["memories"]}


@pytest.mark.fresh_sdk
def test_each_user_reads_only_their_own_library_search_profile_and_graph(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        await _write_both_users(app, sdk, client)
        assert {call["user_id"] for call in sdk.add_calls} == {"local", "wife"}
        # The same caller request id from two users is two SDK requests.
        assert len({call["request_id"] for call in sdk.add_calls}) == len(sdk.add_calls) == 4

        local = (await client.get("/api/v2/memory-library")).json()
        wife = (await client.get("/api/v2/memory-library", params={"user_id": "wife"})).json()
        assert _contents(local) == {"local drinks tea", "local walks the tea route"}
        assert _contents(wife) == {"wife drinks coffee", "wife walks the coffee route"}
        nobody = await client.get("/api/v2/memory-library", params={"user_id": "__nobody__"})
        assert nobody.status_code == 200 and nobody.json()["memories"] == []

        searched = (
            await client.get("/api/v2/memory-library", params={"query": "route", "user_id": "wife"})
        ).json()
        assert _contents(searched) == {"wife walks the coffee route"}

        context = await client.post(
            "/api/v2/context/search",
            json={"query": "drinks", "kinds": ["memory"], "user_id": "wife"},
        )
        assert context.status_code == 200, context.text
        assert {item["content"] for item in context.json()["items"]} == {
            "wife drinks coffee",
            "wife walks the coffee route",
        }
        local_context = await client.post(
            "/api/v2/context/search", json={"query": "drinks", "kinds": ["memory"]}
        )
        assert all("wife" not in item["content"] for item in local_context.json()["items"])

        profile = (await client.get("/api/v2/user-profile", params={"user_id": "wife"})).json()
        assert profile["profile"]["basic_info"] == {"name": "wife"}
        assert (await client.get("/api/v2/user-profile")).json()["profile"]["basic_info"] == {
            "name": "local"
        }

        graph = (await client.get("/api/v2/memories/graph", params={"user_id": "wife"})).json()
        labels = {node["label"] for node in graph["nodes"] if node["node_type"] == "memory"}
        assert labels == {"wife drinks coffee", "wife walks the coffee route"}

        summary = (await client.get("/api/v2/home-summary", params={"user_id": "wife"})).json()
        assert summary["memory_count"] == 2

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_another_users_memory_id_answers_404_for_read_update_and_delete(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        await _write_both_users(app, sdk, client)
        [wife_memory] = [
            memory_id
            for memory_id, row in sdk.memories.items()
            if row["user_id"] == "wife" and row["content"] == "wife drinks coffee"
        ]
        path = f"/api/v2/memories/{wife_memory}"

        assert (await client.get(path)).status_code == 404
        assert (await client.put(path, json={"content": "stolen"})).status_code == 404
        refused = await client.delete(path)
        assert refused.status_code == 404
        assert refused.json()["detail"] == "memory_not_found"
        assert sdk.memories[wife_memory]["content"] == "wife drinks coffee"

        own = await client.get(path, params={"user_id": "wife"})
        assert own.status_code == 200 and own.json()["user_id"] == "wife"
        updated = await client.put(path, json={"content": "wife drinks tea", "user_id": "wife"})
        assert updated.status_code == 200
        assert (await client.delete(path, params={"user_id": "wife"})).status_code == 204
        assert wife_memory not in sdk.memories
        # A second delete (a double click) finds nothing: 404, already gone.
        again = await client.delete(path, params={"user_id": "wife"})
        assert again.status_code == 404
        assert again.json()["detail"] == "memory_not_found"

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_a_delete_that_loses_the_race_answers_404_like_a_missing_id(tmp_path, monkeypatch) -> None:
    """Read as the caller's own, then gone before the SDK deletes it."""

    async def scenario(app, sdk, client):
        await _write_both_users(app, sdk, client)
        [mine] = [
            memory_id
            for memory_id, row in sdk.memories.items()
            if row["content"] == "local drinks tea"
        ]
        original = sdk.delete

        def raced(memory_id, kind=None, variant=None):
            sdk.memories.pop(memory_id, None)
            return original(memory_id, kind=kind, variant=variant)

        sdk.delete = raced  # type: ignore[method-assign]
        response = await client.delete(f"/api/v2/memories/{mine}")
        missing = await client.delete("/api/v2/memories/no-such-memory")
        assert _observed(response) == _observed(missing)
        assert response.status_code == 404

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_receipts_belong_to_the_user_that_submitted_them(tmp_path, monkeypatch) -> None:
    async def scenario(app, sdk, client):
        receipts = await _write_both_users(app, sdk, client)
        wife_add = f"/api/v2/memories/additions/{receipts['add:wife']}"
        assert (await client.get(wife_add)).status_code == 404
        own = await client.get(wife_add, params={"user_id": "wife"})
        assert own.status_code == 200 and own.json()["state"] == "ready"
        local_add = f"/api/v2/memories/additions/{receipts['add:local']}"
        assert (await client.get(local_add, params={"user_id": "wife"})).status_code == 404

        # Both users used `same-request-id`; each reads only their own receipt.
        local_write = await client.get("/api/v2/memory-writes/same-request-id")
        wife_write = await client.get(
            "/api/v2/memory-writes/same-request-id", params={"user_id": "wife"}
        )
        assert local_write.status_code == wife_write.status_code == 200
        assert local_write.json()["memory_ids"] != wife_write.json()["memory_ids"]
        assert (
            await client.get("/api/v2/memory-writes/same-request-id", params={"user_id": "kid"})
        ).status_code == 404
        # `local` cannot name another user's namespaced queue key directly.
        with sqlite3.connect(app.state.settings.data_dir / "one-memory.sqlite3") as db:
            keys = [row[0] for row in db.execute("SELECT idempotency_key FROM memory_write_jobs")]
        wife_key = next(key for key in keys if key.startswith("user:wife:"))
        assert (await client.get(f"/api/v2/memory-writes/{wife_key}")).status_code == 404

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
def test_omitting_user_id_keeps_local_and_recall_and_ask_pass_it_through(
    tmp_path, monkeypatch
) -> None:
    async def scenario(app, sdk, client):
        await _write_both_users(app, sdk, client)
        seen: list[str] = []
        original = sdk.list_memories

        def recording(**kwargs):
            seen.append(kwargs["user_id"])
            return original(**kwargs)

        sdk.list_memories = recording  # type: ignore[method-assign]
        # Built at startup over the real handle; rebuilt here over the stand-in.
        app.state.recall_target = _sdk_recall_target(app, "local")
        await client.get("/api/v2/memory-library")
        await client.get("/api/v2/raw")
        await client.get("/api/v2/home-summary")
        assert set(seen) == {"local"}

        monkeypatch.setattr(
            "one_memory.api._recall_model_ready", lambda _app: _true(), raising=True
        )
        recalled = await client.post("/api/v1/recall", json={"query": "drinks", "user_id": "wife"})
        assert recalled.status_code == 200, recalled.text
        assert {hit["content"] for hit in recalled.json()["hits"]} == {
            "wife drinks coffee",
            "wife walks the coffee route",
        }
        local = await client.post("/api/v1/recall", json={"query": "drinks"})
        assert all("wife" not in hit["content"] for hit in local.json()["hits"])

        answered = await client.post(
            "/api/v2/memories/ask", json={"question": "what does she drink", "user_id": "wife"}
        )
        assert answered.status_code == 200, answered.text
        assert {memory["content"] for memory in answered.json()["memories"]} <= {
            "wife drinks coffee",
            "wife walks the coffee route",
        }

    run_with_sdk(tmp_path, monkeypatch, scenario)


async def _true() -> bool:
    return True


@pytest.mark.fresh_sdk
@pytest.mark.parametrize("user", [None, "wife"])
def test_every_read_echoes_the_user_it_was_read_for(tmp_path, monkeypatch, user) -> None:
    """Each read names the user it answered for: the one asked for, else `local`."""

    async def scenario(app, sdk, client):
        await _write_both_users(app, sdk, client)
        monkeypatch.setattr(
            "one_memory.api._recall_model_ready", lambda _app: _true(), raising=True
        )
        app.state.recall_target = _sdk_recall_target(app, "local")
        expected = user or "local"
        params = {} if user is None else {"user_id": user}
        body = {} if user is None else {"user_id": user}
        reads = [
            await client.get("/api/v2/memory-library", params=params),
            await client.get("/api/v2/memory-library", params={**params, "query": "route"}),
            await client.get("/api/v2/memory-library/layers", params=params),
            await client.get("/api/v2/user-profile", params=params),
            await client.get("/api/v2/home-summary", params=params),
            await client.get("/api/v2/memories/graph", params=params),
            await client.get("/api/v2/raw", params=params),
            await client.post(
                "/api/v2/context/search", json={"query": "drinks", "kinds": ["memory"], **body}
            ),
            await client.post("/api/v1/recall", json={"query": "drinks", **body}),
            await client.post("/api/v2/memories/ask", json={"question": "what drink", **body}),
        ]
        for response in reads:
            assert response.status_code == 200, (response.request.url, response.text)
            assert response.json()["user_id"] == expected, response.request.url

        async with client.stream(
            "POST", "/api/v2/memories/ask/stream", json={"question": "what drink", **body}
        ) as streamed:
            events = [
                json.loads(line[5:])
                async for line in streamed.aiter_lines()
                if line.startswith("data:")
            ]
        assert events[0]["type"] == "recall"
        assert events[0]["user_id"] == expected

    run_with_sdk(tmp_path, monkeypatch, scenario)


@pytest.mark.fresh_sdk
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/v2/memory-library", {"params": {"nobody": "1"}}),
        ("get", "/api/v2/home-summary", {"params": {"userid": "wife"}}),
        ("get", "/api/v2/user-profile", {"params": {"user": "wife"}}),
        ("get", "/api/v2/raw", {"params": {"scope": "x"}}),
        ("get", "/api/v2/memories/graph", {"params": {"scene": "all"}}),
        ("get", "/api/v2/memories/some-id", {"params": {"owner": "wife"}}),
        ("delete", "/api/v2/memories/some-id", {"params": {"owner": "wife"}}),
        ("get", "/api/v2/memory-writes/some-id", {"params": {"owner": "wife"}}),
        ("get", f"/api/v2/memories/additions/{uuid4()}", {"params": {"owner": "wife"}}),
        ("post", "/api/v2/memories/ask", {"json": {"question": "q", "user": "wife"}}),
        ("post", "/api/v1/recall", {"json": {"query": "q", "user": "wife"}}),
        ("post", "/api/v1/recall", {"json": {"query": "q"}, "params": {"user_id": "wife"}}),
        ("post", "/api/v2/context/search", {"json": {"query": "q", "owner": "wife"}}),
        (
            "post",
            "/api/v2/memories",
            {"json": {"request_id": str(uuid4()), "text": "t", "scope": "all-apps", "u": 1}},
        ),
        ("put", "/api/v2/memories/some-id", {"json": {"content": "c", "owner": "wife"}}),
    ],
)
def test_unknown_query_and_body_parameters_answer_422(tmp_path, method, path, kwargs) -> None:
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            response = await client.request(method.upper(), path, **kwargs)
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == "request_validation_failed"

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
@pytest.mark.parametrize("bad", ["a:b", "", "x" * 65, "white space", "ü", "a/b"])
def test_a_malformed_user_id_answers_422(tmp_path, bad) -> None:
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            responses = [
                await client.get("/api/v2/memory-library", params={"user_id": bad}),
                await client.get("/api/v2/user-profile", params={"user_id": bad}),
                await client.post(
                    "/api/v2/memories",
                    json={
                        "request_id": str(uuid4()),
                        "text": "t",
                        "scope": "all-apps",
                        "user_id": bad,
                    },
                ),
                await client.post(
                    "/api/v2/memory-writes",
                    json={
                        "request_id": "r",
                        "session_id": "s",
                        "messages": _messages("hello"),
                        "user_id": bad,
                    },
                ),
                await client.post("/api/v1/recall", json={"query": "q", "user_id": bad}),
            ]
            assert [response.status_code for response in responses] == [422] * len(responses)

    asyncio.run(scenario())


def test_user_ids_and_lanes() -> None:
    assert valid_user_id("wife") and valid_user_id("A.b_c-9")
    assert not valid_user_id("a:b") and not valid_user_id("wife\n")
    assert lane_key("local", "hermes") == "hermes"
    assert lane_key("wife", "hermes") == "hermes@wife"
    assert split_lane("hermes@wife") == ("wife", "hermes")
    assert split_lane("hermes") == ("local", "hermes")
    # `local` keeps the session identity it always had; another user's never equals it.
    assert internal_session_id_for("s-1", "hermes") == "s-1"
    assert internal_session_id_for("s-1", "hermes", "wife") != "s-1"
    assert internal_session_id_for("s-1", "hermes", "wife") != internal_session_id_for(
        "s-1", "hermes", "kid"
    )


def _hermes_state_db(root: Path, rows: list[tuple[int, str, str, str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "state.db")
    connection.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
        " content TEXT, timestamp REAL, tool_name TEXT, active INTEGER)"
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, 1790000000, NULL, 1)",
        rows,
    )
    connection.commit()
    connection.close()


def _hermes_homes(home: Path) -> tuple[Path, Path]:
    hermes = home / ".hermes"
    wife = hermes / "profiles" / "wife"
    # The same Hermes session id in both profiles: identities must not merge.
    _hermes_state_db(
        hermes,
        [(1, "session-1", "user", "I am the husband"), (2, "session-1", "assistant", "hi")],
    )
    _hermes_state_db(
        wife,
        [(1, "session-1", "user", "I am the wife"), (2, "session-1", "assistant", "hello")],
    )
    return hermes, wife


def _scan_texts(root: Path, home: Path) -> list[str]:
    adapter = create_hermes_adapter(
        AuthorizedSourceRoot.grant(
            source_id=HERMES_DESCRIPTOR.source_id, root=root, user_confirmed=True, home=home
        )
    )

    async def collect() -> list[str]:
        return [
            event.evidence.verbatim
            async for event in adapter.scan(ScanOptions(cursors={}))
            if isinstance(event, ScannedEvidence)
        ]

    return asyncio.run(collect())


def test_a_hermes_root_scan_never_reads_its_profiles(tmp_path) -> None:
    hermes, wife = _hermes_homes(tmp_path)
    (hermes / "profiles" / "wife" / "sessions").mkdir()
    (hermes / "profiles" / "wife" / "sessions" / "s.jsonl").write_text(
        json.dumps({"type": "message", "role": "user", "content": "wife's transcript"}) + "\n"
    )

    assert _scan_texts(hermes, tmp_path) == ["I am the husband", "hi"]
    assert _scan_texts(wife, tmp_path) == ["I am the wife", "hello", "wife's transcript"]


def test_a_profiles_directory_is_skipped_at_any_depth(tmp_path) -> None:
    """A root above the Hermes home still never reads `.hermes/profiles/**`."""
    home = tmp_path / "home"
    hermes, _wife = _hermes_homes(home)
    (hermes / "sessions").mkdir()
    (hermes / "sessions" / "own.jsonl").write_text(
        json.dumps({"type": "message", "role": "user", "content": "own transcript"}) + "\n"
    )
    nested = hermes / "sessions" / "profiles" / "kid" / "sessions"
    nested.mkdir(parents=True)
    (nested / "k.jsonl").write_text(
        json.dumps({"type": "message", "role": "user", "content": "kid's transcript"}) + "\n"
    )

    above = _scan_texts(home, tmp_path)
    assert "I am the wife" not in above and "kid's transcript" not in above
    assert set(above) == {"I am the husband", "hi", "own transcript"}
    assert "kid's transcript" not in _scan_texts(hermes, tmp_path)


def _jobs(database: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT session_key,source_key,idempotency_key,state,payload_json "
            "FROM memory_write_jobs ORDER BY queue_seq"
        ).fetchall()


@pytest.mark.fresh_sdk
def test_two_hermes_grants_each_read_their_own_root_into_separate_batches(tmp_path) -> None:
    async def scenario():
        home = tmp_path / "home"
        _hermes, wife = _hermes_homes(home)
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
            profile = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "user_id": "wife",
                    "root": str(wife),
                },
            )
            assert profile.status_code == 200, profile.text
            [wife_job] = profile.json()["jobs"]
            assert wife_job["user_id"] == "wife"
            jobs = [*local.json()["jobs"], wife_job]
            for job in jobs:
                finished = await app.state.import_supervisor.wait(job["job_id"])
                assert finished.status == "completed", finished

            rows = [row for row in _jobs(app.state.settings.data_dir / "one-memory.sqlite3")]
            assert rows, "history import wrote no batches"
            by_user: dict[str, list[str]] = {}
            for row in rows:
                records = json.loads(row["payload_json"])["input_records"]
                users = {record["user_id"] for record in records}
                assert len(users) == 1, "a batch mixed users"
                [user] = users
                by_user.setdefault(user, []).extend(record["verbatim"] for record in records)
                assert row["source_key"] == lane_key(user, "hermes")
                assert row["session_key"] == internal_session_id_for("session-1", "hermes", user)
            assert sorted(by_user["local"]) == ["I am the husband", "hi"]
            assert sorted(by_user["wife"]) == ["I am the wife", "hello"]

            grants = (await client.get("/api/v1/source-grants")).json()["grants"]
            assert {(grant["user_id"], grant["source_id"]) for grant in grants} >= {
                ("local", "hermes"),
                ("wife", "hermes"),
            }
            # The App's view stays `local`'s.
            listed = (await client.get("/api/v1/import-jobs")).json()
            assert {job["user_id"] for job in listed} == {"local"}
            wife_jobs = (await client.get("/api/v1/import-jobs", params={"user_id": "wife"})).json()
            assert {job["user_id"] for job in wife_jobs} == {"wife"}
            assert all(
                item["source_id"] != "hermes@wife"
                for item in (await client.get("/api/v1/live-sync")).json()
            )

            revoked = await client.post("/api/v1/sources/hermes/revoke", params={"user_id": "wife"})
            assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
            local_grant = app.state.source_grant_store.receipt(user_id="local", source_id="hermes")
            assert local_grant.revoked_at is None

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_a_migrated_home_with_the_old_grant_triggers_can_connect_and_revoke(tmp_path) -> None:
    """#233: a home an earlier build migrated kept two `source_grants` triggers
    whose body reads the dropped `governed_memory_evidence`. Every grant write
    then failed, so connecting and revoking answered 503. Startup drops them.
    """
    stale = """
        CREATE TRIGGER memory_visibility_dirty_on_grants{suffix}
        AFTER {event} ON source_grants
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            SELECT DISTINCT link.memory_id
            FROM governed_memory_evidence link
            JOIN evidence e ON e.evidence_id = link.evidence_id
            WHERE e.source_id = NEW.source_id
            ON CONFLICT(memory_id) DO NOTHING;
        END;"""

    async def scenario():
        home = tmp_path / "home"
        _hermes, wife = _hermes_homes(home)
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app):
            database = app.state.settings.data_dir / "one-memory.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS memory_visibility_dirty (memory_id TEXT)")
            db.execute(stale.format(suffix="", event="INSERT"))
            db.execute(stale.format(suffix="_update", event="UPDATE"))

        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            location = (await client.post("/api/v1/sources/hermes/location")).json()
            local = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "source_roots": {"hermes": location["root_id"]},
                },
            )
            assert local.status_code == 200, local.text
            profile = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "user_id": "wife",
                    "root": str(wife),
                },
            )
            assert profile.status_code == 200, profile.text
            for user_id in ("wife", "local"):
                revoked = await client.post(
                    "/api/v1/sources/hermes/revoke", params={"user_id": user_id}
                )
                assert revoked.status_code == 200, revoked.text
                assert revoked.json()["revoked_at"] is not None
        with sqlite3.connect(database) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='trigger' AND tbl_name='source_grants'"
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


async def _connect(client, user_id: str, root: Path | None = None, **extra):
    body: dict[str, object] = {"source_ids": ["hermes"], "request_id": str(uuid4()), **extra}
    if user_id != "local":
        body["user_id"] = user_id
    if root is not None:
        body["root"] = str(root)
    return await client.post("/api/v1/sources/connect", json=body)


@pytest.mark.fresh_sdk
def test_a_hermes_root_that_is_the_profiles_container_is_refused(tmp_path) -> None:
    async def scenario():
        home = tmp_path / "home"
        hermes, _wife = _hermes_homes(home)
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            for user in ("wife", "local"):
                refused = await _connect(client, user, hermes / "profiles")
                assert refused.status_code == 422, refused.text
                assert refused.json()["detail"] == "hermes_root_is_profiles_container"
            assert app.state.source_grant_store.grants() == ()

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_two_users_on_one_hermes_root_or_nested_roots_are_refused(tmp_path) -> None:
    async def scenario():
        home = tmp_path / "home"
        hermes, wife = _hermes_homes(home)
        (wife / "sessions").mkdir()
        link = home / "hermes-link"
        link.symlink_to(hermes, target_is_directory=True)
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            location = (await client.post("/api/v1/sources/hermes/location")).json()
            local = await _connect(client, "local", source_roots={"hermes": location["root_id"]})
            assert local.status_code == 200, local.text
            # Same root, the same root through a symlink, a root inside it
            # that is not `profiles/<name>`, and a root above it.
            for root in (hermes, link, wife / "sessions", home):
                refused = await _connect(client, "kid", root)
                assert refused.status_code == 409, (root, refused.text)
                assert refused.json()["detail"] == "hermes_root_overlaps_other_user"
            # The standard layout: `local` on ~/.hermes, wife on its profile.
            accepted = await _connect(client, "wife", wife)
            assert accepted.status_code == 200, accepted.text
            # Another user on wife's own root is refused too.
            twin = await _connect(client, "wife2", wife)
            assert twin.status_code == 409
            # Once wife's grant is revoked her root is free again.
            await client.post("/api/v1/sources/hermes/revoke", params={"user_id": "wife"})
            assert (await _connect(client, "wife2", wife)).status_code == 200

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_local_cannot_take_another_users_hermes_root(tmp_path) -> None:
    """The check runs for `local`'s own connect too, not only for other users."""

    async def scenario():
        home = tmp_path / "home"
        hermes, _wife = _hermes_homes(home)
        other = home / "elsewhere" / ".hermes"
        _hermes_state_db(other, [(1, "s", "user", "elsewhere")])
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            # wife took ~/.hermes itself, which is `local`'s default root.
            taken = await _connect(client, "wife", hermes)
            assert taken.status_code == 200, taken.text
            location = (await client.post("/api/v1/sources/hermes/location")).json()
            refused = await _connect(client, "local", source_roots={"hermes": location["root_id"]})
            assert refused.status_code == 409, refused.text
            assert refused.json()["detail"] == "hermes_root_overlaps_other_user"
            # `local` on a root of its own that does not overlap is fine.
            assert (await _connect(client, "local", other)).status_code == 200

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_only_hermes_takes_another_user_or_a_custom_root(tmp_path) -> None:
    async def scenario():
        home = tmp_path / "home"
        (home / ".claude" / "projects").mkdir(parents=True)
        app = application(tmp_path, home=home)
        async with app.router.lifespan_context(app), client_for(app) as client:
            for extra in ({"user_id": "wife"}, {"root": str(home / ".claude" / "projects")}):
                response = await client.post(
                    "/api/v1/sources/connect",
                    json={
                        "source_ids": ["claude-code"],
                        "request_id": str(uuid4()),
                        "source_roots": {},
                        **extra,
                    },
                )
                assert response.status_code == 422, response.text
                assert response.json()["detail"] == "user_id_requires_multi_user_source"
            revoke = await client.post(
                "/api/v1/sources/claude-code/revoke", params={"user_id": "wife"}
            )
            assert revoke.status_code == 422
            # Hermes too: another user must name their own root.
            rootless = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["hermes"],
                    "request_id": str(uuid4()),
                    "source_roots": {},
                    "user_id": "wife",
                },
            )
            assert rootless.status_code == 422
            assert rootless.json()["detail"] == "source_root_required_for_user"

    asyncio.run(scenario())


def _record(user_id: str, message_id: str, role: str, text: str):
    return materialize_write_input(
        EvidenceInput(
            user_id=user_id,
            source_id="hermes",
            conversation_id="session-1",
            message_id=message_id,
            role=role,
            written_by=WrittenBy.PERSON if role == "user" else WrittenBy.AGENT,
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            verbatim=text,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )


def test_assembling_rows_and_batches_are_keyed_by_user(tmp_path) -> None:
    """Two users' `state.db` share a relative file key and a session id.

    Finishing one user's file must neither take nor drop the other user's
    staged turns, and the batches it writes hold one user each.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path, sdk_authority=True)
    file_key = "f" * 64
    from one_memory.database_access import main_database

    with main_database(path).write(row_factory=sqlite3.Row) as connection, connection:
        for user in ("local", "wife"):
            for index, role in enumerate(("user", "assistant")):
                queue.stage_sdk_input(
                    _record(user, f"m{index}", role, f"{user} {role}"),
                    file_key=file_key,
                    priority="normal",
                    connection=connection,
                )
        queue.finalize_sdk_file(
            user_id="local", source_id="hermes", file_key=file_key, connection=connection
        )

    rows = _jobs(path)
    pending = [row for row in rows if row["state"] == "pending"]
    assembling = [row for row in rows if row["state"] == "assembling"]
    assert [
        {record["user_id"] for record in json.loads(row["payload_json"])["input_records"]}
        for row in pending
    ] == [{"local"}]
    assert [split_lane(row["source_key"]) for row in assembling] == [("wife", "hermes")]
    assert pending[0]["session_key"] != assembling[0]["session_key"]


def _observed(response) -> tuple[int, bytes]:
    return response.status_code, response.content


@pytest.mark.fresh_sdk
def test_another_users_id_answers_exactly_like_an_id_that_does_not_exist(
    tmp_path, monkeypatch
) -> None:
    """Comparing responses must not tell whether another user's id exists."""

    async def scenario(app, sdk, client):
        receipts = await _write_both_users(app, sdk, client)
        [wife_memory] = [
            memory_id
            for memory_id, row in sdk.memories.items()
            if row["user_id"] == "wife" and row["content"] == "wife drinks coffee"
        ]
        before = {key: dict(row) for key, row in sdk.memories.items()}
        with sqlite3.connect(app.state.settings.data_dir / "one-memory.sqlite3") as db:
            keys = [row[0] for row in db.execute("SELECT idempotency_key FROM memory_write_jobs")]
        wife_key = next(key for key in keys if key.startswith("user:wife:"))

        pairs = [
            ("GET", f"/api/v2/memories/{wife_memory}", "/api/v2/memories/no-such-memory", {}),
            (
                "PUT",
                f"/api/v2/memories/{wife_memory}",
                "/api/v2/memories/no-such-memory",
                {"json": {"content": "stolen"}},
            ),
            ("DELETE", f"/api/v2/memories/{wife_memory}", "/api/v2/memories/no-such-memory", {}),
            (
                "GET",
                f"/api/v2/memories/additions/{receipts['add:wife']}",
                f"/api/v2/memories/additions/{uuid4()}",
                {},
            ),
            ("GET", f"/api/v2/memory-writes/{wife_key}", "/api/v2/memory-writes/no-such-id", {}),
        ]
        for method, theirs, missing, kwargs in pairs:
            other = await client.request(method, theirs, **kwargs)
            absent = await client.request(method, missing, **kwargs)
            assert _observed(other) == _observed(absent), (method, theirs)
            assert other.status_code == 404
        assert sdk.memories == before

    run_with_sdk(tmp_path, monkeypatch, scenario)
