"""R-27: a correction that only lands locally, while HY keeps the old text.

Deleting a memory cascades to HY (`api.py:1129` → `bridge.delete`). Correcting
one does not: `POST /api/v1/memories/{id}/correct` writes the governance
record and stops. The semantic half of recall comes from HY, so after a
correction HY still matches and ranks on the old wording while the governance
store returns the new — the user fixed it, saw it fixed, and search kept
using the sentence they replaced.

`client.update(memory_id, content)` re-embeds and upserts in place, keeping
the id (`hy_memory/client.py:2500-2561`), so the capability was already here
and simply never called.
"""

from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.hy_memory_bridge import HyMemoryBridgeError
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ProviderKind,
)
from one_memory.recall import RecallRequest, RecallState


class FakeHy:
    """HY's side of the seam: an index keyed by memory id, plus a kill switch.

    Holds content rather than just recording calls, so a test can ask the
    question the user asks — does searching the new wording find it, and does
    the old wording stop finding it.
    """

    def __init__(self) -> None:
        self.index: dict[str, str] = {}
        self.available = True
        #: HY holds no such memory, so no retry can ever land it.
        self.rejects = False
        self.updates: list[tuple[str, str]] = []
        self.deletes: list[str] = []
        self.adds: list[object] = []

    async def update(self, memory_id: str, content: str) -> dict[str, object]:
        if not self.available:
            raise HyMemoryBridgeError("hy_memory_update_failed")
        if self.rejects:
            raise HyMemoryBridgeError("hy_memory_update_rejected")
        self.updates.append((memory_id, content))
        self.index[memory_id] = content
        return {"success": True, "memory_id": memory_id}

    async def delete(self, memory_id: str) -> dict[str, object]:
        self.deletes.append(memory_id)
        self.index.pop(memory_id, None)
        return {"success": True}

    async def ingest(self, request: object) -> object:
        self.adds.append(request)
        raise AssertionError("a correction must not re-add the memory")

    async def search_ids(self, query: str, **_kwargs) -> tuple[str, ...]:
        return tuple(memory_id for memory_id, content in self.index.items() if query in content)

    async def close(self) -> None:
        return None


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr("local-token"),
    )


def _endpoint(app, path: str):
    """The route's own function, so a test can hold the response and the work
    scheduled after it apart — over HTTP the two are indistinguishable."""
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no route at {path}")


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer local-token",
            "Origin": "http://127.0.0.1:5173",
        },
    )


async def _confirmed_memory(app, client: AsyncClient, content: str, tag: str = "1") -> str:
    # `tag` keeps the request ids apart. They are the mutation ledger's key, so
    # a second memory created under the first one's id would be served the
    # first one's result as a replay and never exist.
    evidence = app.state.evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id=f"conversation-{tag}",
            message_id=f"message-{tag}",
            correlation_id=f"message-{tag}",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=content,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={})},
        )
    ).record
    created = await client.post(
        "/api/v1/memories",
        json={
            "request_id": f"create-{tag}",
            "draft": {
                "user_id": "local",
                "kind": "chat",
                "content": content,
                "scopes": ["all-apps"],
                "evidence_ids": [evidence.evidence_id],
            },
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["memory_id"]
    return memory_id


def _attach(app, hy: FakeHy) -> None:
    """Put the fake where the supervisor resolves its bridge.

    The correction path has to go through the supervisor like deletion does,
    because that is where the active profile and its secret are resolved —
    which is also why the app needs an active profile before either cascade
    can reach HY at all.

    `supervisor._bridge` built `HyMemoryBridge` directly until R-27 and never
    consulted `bridge_factory`, so neither cascade had a seam and the delete
    one had no test. Setting the factory is now enough to stay off the
    network, which is why the delete control below can exist at all.
    """
    profiles = app.state.model_profile_store
    secrets = app.state.secret_store
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="hy-bot-phi4",
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
    supervisor = app.state.memory_processing_supervisor
    supervisor.bridge_factory = lambda _profile, _secret, _data_dir: hy


def test_remote_correction_updates_sdk_without_retired_content_consent(tmp_path):
    async def scenario():
        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app), _client(app) as client:
            hy = FakeHy()
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "original")
            profiles, secrets = app.state.model_profile_store, app.state.secret_store
            profile = profiles.create(
                ModelProfileInput(
                    name="Remote embedding",
                    provider_kind=ProviderKind.OLLAMA,
                    model="fixture",
                    embedding_model="remote-embedding",
                    embedding_provider_kind=ProviderKind.OLLAMA,
                    embedding_base_url="http://127.0.0.1:1/v1",
                ),
                secrets,
            )
            profiles.record_capabilities(
                profile.profile_id,
                expected_revision=profile.revision,
                capabilities=ModelCapabilities(
                    endpoint_reachable=True, model_available=True, embedding_available=True
                ),
                secrets=secrets,
            )
            profiles.activate(profile.profile_id, secrets)
            changed = await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "local-edit",
                    "expected_revision": 1,
                    "content": "updated",
                    "scopes": ["all-apps"],
                },
            )
            assert changed.status_code == 200
            assert changed.json()["content"] == "updated"
            assert hy.updates == [(memory_id, "updated")]

    asyncio.run(scenario())


def test_correcting_a_memory_updates_hy_in_place(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"

            corrected = await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

            assert corrected.status_code == 200
            assert hy.updates == [(memory_id, "用户的花名是白泽")]
            assert hy.index[memory_id] == "用户的花名是白泽"
            # The boundary: never delete-and-re-add, which would change the id
            # and break every reference already handed out.
            assert hy.deletes == []
            assert hy.adds == []
            assert corrected.json()["memory_id"] == memory_id

    asyncio.run(scenario())


def test_search_follows_the_correction(tmp_path) -> None:
    """The point of the sync: the new wording is findable, the old is not."""

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"

            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

            assert await hy.search_ids("白泽") == (memory_id,)
            assert await hy.search_ids("麒麟") == ()

    asyncio.run(scenario())


def test_a_correction_is_not_lost_when_hy_is_down(tmp_path) -> None:
    """The user's intent outranks the downstream.

    The correction lands locally whatever HY does, and the fact that HY is
    behind is recorded rather than swallowed.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"
            hy.available = False

            corrected = await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

            assert corrected.status_code == 200
            assert corrected.json()["content"] == "用户的花名是白泽"
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            sync = detail.json()["index_sync"]
            assert sync["status"] == "pending"
            assert sync["attempts"] == 1
            assert sync["last_error_code"] == "hy_memory_update_failed"

    asyncio.run(scenario())


def test_the_app_can_tell_the_user_the_index_is_current(tmp_path) -> None:
    """A synced correction must be distinguishable from a pending one.

    Otherwise the interface has to choose between claiming success it cannot
    see and worrying the user every time.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"

            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

            detail = await client.get(f"/api/v1/memories/{memory_id}")
            assert detail.json()["index_sync"]["status"] == "synced"

    asyncio.run(scenario())


def test_a_retry_after_hy_returns_reaches_consistency(tmp_path) -> None:
    """ "Eventually consistent" needs something that actually retries.

    The retry sends the memory's current content rather than a copy taken at
    correction time, so a memory corrected twice while HY was down converges
    on the latest wording rather than replaying an intermediate one.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"
            hy.available = False
            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )
            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-2",
                    "expected_revision": 2,
                    "content": "用户的花名是白泽，读作 bái zé",
                    "scopes": ["all-apps"],
                },
            )
            hy.available = True

            retried = await client.post(f"/api/v1/memories/{memory_id}/index-sync")

            assert retried.status_code == 200
            assert retried.json()["status"] == "synced"
            assert hy.index[memory_id] == "用户的花名是白泽，读作 bái zé"
            assert len(hy.updates) == 1
            # Both failed corrections and the retry that worked: the count is
            # how anyone tells a downstream that is slow from one that is
            # broken, so a second correction must not restart it at zero.
            assert retried.json()["attempts"] == 3

    asyncio.run(scenario())


def test_a_pending_sync_outlives_the_process(tmp_path) -> None:
    """Restarting must not lose the knowledge that HY is stale.

    A correction the user made and a downstream that never caught up is
    exactly the state a crash would otherwise erase, leaving a memory that
    searches wrong with nothing recording why.
    """

    async def scenario() -> None:
        hy = FakeHy()
        settings = _settings(tmp_path)
        app = create_app(settings)
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.available = False
            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

        # A second app over the same data_dir: new stores, nothing carried
        # over in memory, so anything it still knows it read back from disk.
        restarted = create_app(settings)
        async with restarted.router.lifespan_context(restarted), _client(restarted) as client:
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            sync = detail.json()["index_sync"]
            assert sync["status"] == "pending"
            assert sync["last_error_code"] == "hy_memory_update_failed"

    asyncio.run(scenario())


def test_undoing_a_correction_syncs_hy_back_too(tmp_path) -> None:
    """Undo moves the text as much as correcting it did.

    Taking a correction back and leaving HY matching on the withdrawn wording
    is the same defect from the other direction, and the one the user reaches
    for right after a correction they regret.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.index[memory_id] = "用户的花名是麒麟"
            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )

            undone = await client.post(
                f"/api/v1/memories/{memory_id}/actions/undo",
                json={"request_id": "undo-1", "expected_revision": 2},
            )

            assert undone.status_code == 200
            assert undone.json()["content"] == "用户的花名是麒麟"
            assert hy.index[memory_id] == "用户的花名是麒麟"
            assert await hy.search_ids("白泽") == ()
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            assert detail.json()["index_sync"]["status"] == "synced"

    asyncio.run(scenario())


def test_an_untouched_memory_has_nothing_to_sync(tmp_path) -> None:
    """The control: a memory nobody corrected must not look out of date.

    Green before and after. If every memory reported a sync state, the
    interface would have to explain a status the user never caused — and
    confirming this one moved its status without moving a word of its text,
    which is the distinction the reindex has to be keyed on.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")

            detail = await client.get(f"/api/v1/memories/{memory_id}")

            assert detail.json()["index_sync"] is None
            assert hy.updates == []

    asyncio.run(scenario())


async def _stale_memory(app, client: AsyncClient, hy: FakeHy) -> str:
    """A memory the user corrected while HY was unreachable.

    The state this whole section is about: the app reads 白泽, HY still
    matches on 麒麟, and nothing has caught up.
    """
    memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
    hy.index[memory_id] = "用户的花名是麒麟"
    hy.available = False
    await client.post(
        f"/api/v1/memories/{memory_id}/correct",
        json={
            "request_id": "correct-1",
            "expected_revision": 1,
            "content": "用户的花名是白泽",
            "scopes": ["all-apps"],
        },
    )
    hy.available = True
    return memory_id


def test_a_recall_that_returns_a_stale_memory_catches_hy_up(tmp_path) -> None:
    """Where the retry belongs: the moment the memory is actually read.

    Not a sweep at startup — the night the office lost its outbound network,
    calling HY on launch would have turned one outage into an app that will
    not open. A memory's index being behind can only be observed through
    recall, so recall is where catching up is worth anything.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _stale_memory(app, client, hy)

            recalled = await client.post(
                "/api/v1/recall",
                json={"query": "白泽", "scope": "all-apps"},
            )

            assert recalled.status_code == 200
            assert recalled.json()["state"] == "grounded"
            assert hy.index[memory_id] == "用户的花名是白泽"
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            assert detail.json()["index_sync"]["status"] == "synced"

    asyncio.run(scenario())


def test_the_recall_itself_does_not_wait_on_hy_being_back(tmp_path) -> None:
    """The recall answers from the governance store, which already holds the
    corrected text. Letting a still-unreachable HY turn that into an error, or
    into a wait, would make the fix worse than the defect."""

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _stale_memory(app, client, hy)
            hy.available = False

            recalled = await client.post(
                "/api/v1/recall",
                json={"query": "白泽", "scope": "all-apps"},
            )

            assert recalled.status_code == 200
            assert recalled.json()["state"] == "grounded"
            assert recalled.json()["hits"][0]["content"] == "用户的花名是白泽"
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            sync = detail.json()["index_sync"]
            assert sync["status"] == "pending"
            assert sync["attempts"] == 2

    asyncio.run(scenario())


def test_a_memory_no_recall_returned_is_left_alone(tmp_path) -> None:
    """Scoping the work to what was read is the whole reason this is cheap.

    A memory nobody recalls is a memory nobody reads, so its index being
    behind is a difference no user can observe — and paying for it would mean
    a call to HY per stale memory on every recall.

    So the recall here does ground on something: a catch-up that skipped this
    memory only because nothing at all was returned would prove nothing.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            stale_id = await _stale_memory(app, client, hy)
            await _confirmed_memory(app, client, "用户在用 Cursor 写代码", tag="2")

            recalled = await client.post(
                "/api/v1/recall",
                json={"query": "Cursor", "scope": "all-apps"},
            )

            assert recalled.status_code == 200
            assert recalled.json()["state"] == "grounded"
            assert stale_id not in {hit["memory_id"] for hit in recalled.json()["hits"]}
            assert hy.updates == []
            assert hy.index[stale_id] == "用户的花名是麒麟"

    asyncio.run(scenario())


def test_the_recall_answers_before_the_catch_up_runs(tmp_path) -> None:
    """The ordering is the reason recall is a safe place to do this at all.

    Called through the route so the two halves are separable: the receipt is
    built without HY in the picture, and the catch-up is left as work for
    afterwards. Done inline instead, every recall would wait on a network
    call — the exact cost that ruled out doing this at startup.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _stale_memory(app, client, hy)
            recall = _endpoint(app, "/api/v1/recall")
            background = BackgroundTasks()

            receipt = await recall(
                RecallRequest(query="白泽", scope="all-apps"),
                background,
                Request({"type": "http", "headers": []}),
            )

            assert receipt.state is RecallState.GROUNDED
            assert hy.updates == []
            assert background.tasks

            await background()

            assert hy.index[memory_id] == "用户的花名是白泽"

    asyncio.run(scenario())


def test_a_retry_hy_can_never_accept_is_not_made_on_every_recall(tmp_path) -> None:
    """`hy_memory_update_rejected` means HY has no such memory to re-embed.

    Retrying it is a call that cannot succeed, and recall is the one path that
    runs constantly — so this is the difference between a harmless catch-up
    and a request to HY every time anyone recalls anything.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")
            hy.rejects = True
            await client.post(
                f"/api/v1/memories/{memory_id}/correct",
                json={
                    "request_id": "correct-1",
                    "expected_revision": 1,
                    "content": "用户的花名是白泽",
                    "scopes": ["all-apps"],
                },
            )
            hy.updates.clear()

            await client.post(
                "/api/v1/recall",
                json={"query": "白泽", "scope": "all-apps"},
            )

            assert hy.updates == []
            detail = await client.get(f"/api/v1/memories/{memory_id}")
            sync = detail.json()["index_sync"]
            assert sync["status"] == "pending"
            assert sync["attempts"] == 1

    asyncio.run(scenario())


def test_recall_touches_hy_for_nothing_when_no_memory_is_behind(tmp_path) -> None:
    """The control: recall is the hot path, and it stays quiet.

    Green before and after. Every recall in the product runs through here, so
    a catch-up that fired on memories nobody corrected would be a permanent
    tax on the one call that has to stay fast.
    """

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            await _confirmed_memory(app, client, "用户的花名是麒麟")

            recalled = await client.post(
                "/api/v1/recall",
                json={"query": "麒麟", "scope": "all-apps"},
            )

            assert recalled.json()["state"] == "grounded"
            assert hy.updates == []

    asyncio.run(scenario())


def test_deletion_still_cascades_the_way_it_did(tmp_path) -> None:
    """The control for the path that already worked."""

    async def scenario() -> None:
        app = create_app(_settings(tmp_path))
        hy = FakeHy()
        async with app.router.lifespan_context(app), _client(app) as client:
            _attach(app, hy)
            memory_id = await _confirmed_memory(app, client, "用户的花名是麒麟")

            deleted = await client.post(
                f"/api/v1/memories/{memory_id}/actions/delete",
                json={"request_id": "delete-1", "expected_revision": 1},
            )

            assert deleted.status_code == 200
            assert hy.deletes == [memory_id]

    asyncio.run(scenario())
