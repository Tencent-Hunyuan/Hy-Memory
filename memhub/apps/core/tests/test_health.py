import asyncio
import logging
import sqlite3
import threading
import traceback
from contextlib import suppress

import pytest
from httpx import ASGITransport, AsyncClient, Response

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.unified_entry import UnifiedWriteIntake
from one_memory.model_profiles import ModelCapabilities, ModelProfileInput, ModelProfileStore
from one_memory.secrets import InMemorySecretStore


async def request(
    method: str,
    headers: dict[str, str] | None = None,
    path: str = "/api/v1/health",
) -> Response:
    settings = Settings(instance_id="test-instance")
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers)


def test_health_reports_the_single_core() -> None:
    response = asyncio.run(request("GET"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "one-memory-core",
        "version": "development",
        "instance_id": "test-instance",
        "memory_backfill": "done",
        "memory_backfill_error": None,
        "content_candidate_backfill": "idle",
        "content_candidate_backfill_failure": None,
    }


def test_core_refuses_to_start_a_database_written_by_a_newer_build(tmp_path, monkeypatch) -> None:
    """The product startup path must fail closed on a future schema."""
    from one_memory.schema_gate import BUILD_SCHEMA_VERSION

    database = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?,?,?)",
            (BUILD_SCHEMA_VERSION + 1, "future", "2026-09-29T00:00:00+00:00"),
        )
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "source",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )

    async def scenario() -> None:
        with pytest.raises(Exception, match="database_schema_too_new"):
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(scenario())


def test_lifespan_starts_the_content_candidate_backfill_and_exposes_running(
    tmp_path, monkeypatch
) -> None:
    """Deleting the lifespan call must make this red, not leave idle green."""
    entered = threading.Event()
    original_start = MemoryGovernanceStore.start_content_candidate_backfill

    def observed_start(store, **kwargs) -> bool:
        entered.set()
        return original_start(store, **kwargs)

    monkeypatch.setattr(MemoryGovernanceStore, "start_content_candidate_backfill", observed_start)
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "source",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert entered.is_set()
            health = await _get_json(app, "/api/v1/health")
            # An empty library can finish before the first health read, so
            # assert the observable transition, not a particular race frame.
            # Delete the lifespan call and `entered` never fires.
            assert health["content_candidate_backfill"] in {"running", "ready"}

    asyncio.run(scenario())


def test_lifespan_content_backfill_failure_is_visible(tmp_path, monkeypatch) -> None:
    """A failed derived index must be observable without failing startup."""

    def fail_locked(_store: MemoryGovernanceStore) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        MemoryGovernanceStore,
        "_rebuild_content_shadow_batched",
        fail_locked,
    )
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "source",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            health = await _get_json(app, "/api/v1/health")
            assert health["content_candidate_backfill"] == "failed"
            assert health["content_candidate_backfill_failure"] == "database_locked"

    asyncio.run(scenario())


def test_lifespan_drains_committed_pending_dispatch_through_enqueue(tmp_path, monkeypatch) -> None:
    """A committed Evidence row must reach the existing enqueue path after restart."""
    database = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(database)
    record = evidence.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            message_id="pending-dispatch",
            correlation_id="pending-dispatch",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="committed before the callback crashed",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    _ = evidence.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            message_id="pending-dispatch-reply",
            correlation_id="pending-dispatch-reply",
            role="assistant",
            written_by=WrittenBy.AGENT,
            verbatim="reply committed before the callback crashed",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    with evidence._connect(writing=True) as connection:
        evidence.stage_memory_dispatch(record, file_key="f" * 64, connection=connection)

    seen: list[str] = []

    def observe(_self, records, *, priority) -> None:
        del priority
        seen.extend(item.evidence_id for item in records)

    monkeypatch.setattr(
        UnifiedWriteIntake,
        "submit_records",
        observe,
    )
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "source",
            imports_enabled=False,
            automatic_memory_processing=True,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert record.evidence_id in seen
            assert evidence.take_all_stable_records() == ()

    asyncio.run(scenario())


def test_lifespan_keeps_pending_dispatch_when_automatic_processing_is_off(
    tmp_path, monkeypatch
) -> None:
    """Turning processing off must not consume work the user may enable later."""
    database = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(database)
    record = evidence.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            message_id="pending-dispatch-off",
            correlation_id="pending-dispatch-off",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="keep this until processing is enabled",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    with evidence._connect(writing=True) as connection:
        evidence.stage_memory_dispatch(record, file_key="g" * 64, connection=connection)

    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "source",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert [item.evidence_id for item in evidence.take_all_stable_records()] == [
                record.evidence_id
            ]

    asyncio.run(scenario())


async def _get_json(app, path: str) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    return response.json()


def test_health_allows_only_the_local_web_origin() -> None:
    response = asyncio.run(
        request(
            "OPTIONS",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_diagnostic_settings_allows_desktop_put_preflight() -> None:
    response = asyncio.run(
        request(
            "OPTIONS",
            path="/api/v1/diagnostics/settings",
            headers={
                "Origin": "one-memory://app",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "one-memory://app"
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_diagnostic_log_level_allows_desktop_put_preflight() -> None:
    response = asyncio.run(
        request(
            "OPTIONS",
            path="/api/v1/diagnostics/log-level",
            headers={
                "Origin": "one-memory://app",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "one-memory://app"
    assert "PUT" in response.headers["access-control-allow-methods"]


def core(tmp_path):
    database = tmp_path / "data/one-memory.sqlite3"
    root = tmp_path / "source"
    root.mkdir()
    sources = SourceGrantStore(database, home=tmp_path)
    sources.grant(
        user_id="local", source_id="fixture", root=root, candidate_kind=RootCandidateKind.DEFAULT
    )
    evidence = EvidenceStore(database)
    _record = evidence.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="health",
            message_id="health",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="Synthetic authorized startup work.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    profiles, secrets = ModelProfileStore(database), InMemorySecretStore()
    profile = profiles.create(
        ModelProfileInput(
            name="Health fixture",
            provider_kind="ollama",
            base_url="http://127.0.0.1:1",
            model="fixture",
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
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            instance_id="test-instance",
            imports_enabled=False,
            live_sync_enabled=False,
        ),
        secret_store=secrets,
    )


def test_startup_finishes_before_the_memory_backfill_does(tmp_path, monkeypatch) -> None:
    """Startup must not wait on work that grows with the corpus.

    Reading every conversation to find the ones with no memory yet took 13.5
    seconds on this machine's real database, and it gets slower with every
    import. Run inline it was the last thing standing between the desktop app
    and a Core it had given up waiting for.
    """
    walking = threading.Event()
    release = threading.Event()

    def blocking(
        _: DurableWriteQueue,
        *,
        limit: int = 512,
        after_cursor: str | None = None,
    ):
        walking.set()
        assert release.wait(10)
        return ()

    monkeypatch.setattr(DurableWriteQueue, "unsubmitted_evidence_ids", blocking)

    async def scenario() -> None:
        app = core(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            # Waited off the loop, because waiting on it is what this test is
            # about: a loop blocked here would prove nothing.
            assert await asyncio.to_thread(walking.wait, 10), "the catch-up never started"

            serving = await client.get("/api/v1/health")

            assert serving.status_code == 200
            assert serving.json()["memory_backfill"] == "running"
            assert serving.json()["memory_backfill_error"] is None

            release.set()
            await app.state.memory_backfill
            finished = await client.get("/api/v1/health")

            assert finished.json()["memory_backfill"] == "done"

    asyncio.run(scenario())


def test_a_backfill_that_fails_says_so_rather_than_disappearing(tmp_path, monkeypatch) -> None:
    def broken(
        _: DurableWriteQueue,
        *,
        limit: int = 512,
        after_cursor: str | None = None,
    ):
        raise RuntimeError("the catch-up broke")

    monkeypatch.setattr(DurableWriteQueue, "unsubmitted_evidence_ids", broken)

    async def scenario() -> None:
        app = core(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            await app.state.memory_backfill

            response = await client.get("/api/v1/health")

            assert response.status_code == 200
            assert response.json()["memory_backfill"] == "failed"
            assert "the catch-up broke" in response.json()["memory_backfill_error"]

    asyncio.run(scenario())


def test_shutting_down_mid_backfill_leaves_nothing_running(tmp_path, monkeypatch) -> None:
    """The catch-up holds database connections; the lock outlives it otherwise."""
    started = threading.Event()

    def slow(
        _: DurableWriteQueue,
        *,
        limit: int = 512,
        after_cursor: str | None = None,
    ):
        started.set()
        threading.Event().wait(0.2)
        return ()

    monkeypatch.setattr(DurableWriteQueue, "unsubmitted_evidence_ids", slow)

    async def scenario() -> None:
        app = core(tmp_path)
        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(started.wait, 10)

        assert app.state.memory_backfill.done()

    asyncio.run(scenario())


def test_a_failed_catch_up_leaves_a_traceback_behind(tmp_path, monkeypatch, caplog) -> None:
    """When the catch-up dies, no further memories are made. Say where.

    This really happened on this machine: `/health` reported
    `OperationalError('database is locked')` and that string was the entire
    record. The whole corpus went unextracted and there was nothing anywhere
    naming the store, the method or the query, so the only way forward was to
    guess.
    """

    def failing(
        _: DurableWriteQueue,
        *,
        limit: int = 512,
        after_cursor: str | None = None,
    ):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(DurableWriteQueue, "unsubmitted_evidence_ids", failing)

    async def scenario() -> dict:
        app = core(tmp_path)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            with suppress(BaseException):
                await app.state.memory_backfill
            return (await client.get("/api/v1/health")).json()

    with caplog.at_level(logging.ERROR):
        reported = asyncio.run(scenario())

    # Still reported where it was reported before, so this adds rather than moves.
    assert reported["memory_backfill"] == "failed"
    assert "database is locked" in reported["memory_backfill_error"]
    # And now also written down, with the stack that says where from.
    complaints = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert complaints, "追赶失败了却没有任何日志"
    assert any(record.exc_info is not None for record in complaints), (
        "记了一行日志但没有栈，等于还是只有那句 database is locked"
    )
    frames = [
        frame
        for record in complaints
        if record.exc_info is not None
        for frame in traceback.extract_tb(record.exc_info[2])
    ]
    assert any(frame.name == "_run_backfill" for frame in frames)
    assert any(frame.name == "failing" and frame.filename == __file__ for frame in frames), (
        "栈里没有出事的那个调用"
    )
