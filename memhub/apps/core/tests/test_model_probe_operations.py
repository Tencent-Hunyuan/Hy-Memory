"""Persisted probe identity, real SQLite/API and synthetic transport only."""

import asyncio
import sqlite3
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from one_memory.model_probe import ModelProber
from one_memory.model_probe_operations import (
    ModelProbeOperations,
    ProbeOperationConflict,
    ProbeOperationUnavailable,
)
from one_memory.model_profiles import ModelProfileInput, ModelProfilePatch, ModelProfileStore
from one_memory.secrets import InMemorySecretStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    store = ModelProfileStore(tmp_path / "probe.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="fixture",
            provider_kind="openai_compatible",
            model="fixture-model",
            base_url="https://fixture.invalid/v1",
            api_key="synthetic-key",
            embedding_provider_kind="openai_compatible",
            embedding_model="fixture-vector",
            embedding_base_url="https://fixture.invalid/v1",
            embedding_dimensions=2,
        ),
        secrets,
    )
    operations = ModelProbeOperations(store)
    calls = []

    def response(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"memory_probe":true}'}}]}
        )

    return store, secrets, profile, operations, calls, response


def test_reserved_before_transport_and_atomic_result_reopens_without_resending(env):
    store, secrets, profile, ops, calls, respond = env
    operation = str(uuid4())

    def transport(request):
        pending = ops.lookup(operation)
        assert pending.state == "pending" and pending.result is None
        return respond(request)

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(transport))
        result = await prober.probe(
            profile.profile_id, operation_id=operation, expected_profile_revision=1
        )
        assert result.state == "completed" and result.stale is False
        assert result.result.input_profile_revision == 1 and result.result.profile_revision == 2
        saved = store.require(profile.profile_id, secrets)
        assert saved.revision == 2 and saved.embedding_dimensions == 3
        assert saved.capabilities == result.result.capabilities and not saved.is_active
        assert ModelProbeOperations(store).lookup(operation) == result
        assert (
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=1
            )
            == result
        )
        assert len(calls) == 3
        assert "synthetic-key" not in result.model_dump_json()
        with closing(sqlite3.connect(store.database_path)) as db:
            rows = list(db.execute("SELECT * FROM model_probe_operations"))
            assert "synthetic-key" not in repr(rows) and "fixture.invalid" not in repr(rows)

    asyncio.run(scenario())


def test_same_id_and_same_profile_concurrency_refused_before_second_call(env):
    store, secrets, profile, ops, calls, respond = env

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        operation = str(uuid4())

        async def transport(request):
            entered.set()
            await release.wait()
            return respond(request)

        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(transport))
        task = asyncio.create_task(
            prober.probe(profile.profile_id, operation_id=operation, expected_profile_revision=1)
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            for identifier in (operation, str(uuid4())):
                with pytest.raises(ProbeOperationConflict):
                    await prober.probe(
                        profile.profile_id, operation_id=identifier, expected_profile_revision=1
                    )
            assert ops.lookup(operation).state == "pending"
        finally:
            release.set()
            await task
        assert len(calls) == 3

    asyncio.run(scenario())


def test_cancel_after_dispatch_remains_pending_across_reopen(env):
    store, secrets, profile, ops, calls, _ = env

    async def scenario():
        entered = asyncio.Event()
        operation = str(uuid4())

        async def transport(request):
            calls.append(request.url.path)
            entered.set()
            await asyncio.Event().wait()

        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(transport))
        task = asyncio.create_task(
            prober.probe(profile.profile_id, operation_id=operation, expected_profile_revision=1)
        )
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ModelProbeOperations(store).lookup(operation).state == "pending"
        for identifier in (operation, str(uuid4())):
            with pytest.raises(ProbeOperationConflict):
                await prober.probe(
                    profile.profile_id, operation_id=identifier, expected_profile_revision=1
                )
        assert len(calls) == 1 and store.require(profile.profile_id, secrets).revision == 1

    asyncio.run(scenario())


def test_profile_change_during_probe_never_publishes_stale_capabilities(env):
    store, secrets, profile, ops, calls, respond = env
    operation = str(uuid4())

    def transport(request):
        if not calls:
            store.update(
                profile.profile_id, ModelProfilePatch(expected_revision=1, model="changed"), secrets
            )
        return respond(request)

    result = asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(transport)).probe(
            profile.profile_id,
            operation_id=operation,
            expected_profile_revision=1,
        )
    )
    assert result.state == "failed" and result.code == "model_probe_profile_changed"
    assert len(calls) == 1
    assert not store.require(profile.profile_id, secrets).capabilities.model_available
    assert ops.lookup(operation) == result


def test_result_and_width_rollback_together_when_receipt_commit_fails(env):
    store, secrets, profile, ops, calls, respond = env
    operation = str(uuid4())

    def transport(request):
        if request.url.path.endswith("/embeddings"):
            with sqlite3.connect(store.database_path) as db:
                db.execute(
                    "CREATE TRIGGER deny_probe_result BEFORE UPDATE ON model_probe_operations "
                    "BEGIN SELECT RAISE(ABORT,'fixture'); END"
                )
        return respond(request)

    with pytest.raises(ProbeOperationUnavailable):
        asyncio.run(
            ModelProber(
                store=store, secrets=secrets, transport=httpx.MockTransport(transport)
            ).probe(
                profile.profile_id,
                operation_id=operation,
                expected_profile_revision=1,
            )
        )
    assert len(calls) == 3
    assert ops.lookup(operation).state == "pending"
    saved = store.require(profile.profile_id, secrets)
    assert saved.revision == 1 and saved.embedding_dimensions == 2
    assert not saved.capabilities.model_available


def test_known_negative_result_is_queryable_but_never_auto_reprobed(env):
    store, secrets, profile, ops, calls, _ = env
    operation = str(uuid4())

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(401)

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(transport))
        result = await prober.probe(
            profile.profile_id, operation_id=operation, expected_profile_revision=1
        )
        assert result.state == "completed" and not result.result.capabilities.model_available
        assert ops.lookup(operation) == result
        assert (
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=1
            )
            == result
        )
        assert len(calls) == 1

    asyncio.run(scenario())


def test_lookup_is_read_only_and_does_not_consult_secrets(env, monkeypatch):
    store, secrets, profile, ops, _, respond = env
    missing = str(uuid4())
    assert ops.lookup(missing).state == "not_found"
    with closing(sqlite3.connect(store.database_path)) as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='model_probe_operations'").fetchone()
            is None
        )
    operation = str(uuid4())
    asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
            profile.profile_id,
            operation_id=operation,
            expected_profile_revision=1,
        )
    )
    store.update(
        profile.profile_id, ModelProfilePatch(expected_revision=2, name="changed"), secrets
    )
    monkeypatch.setattr(secrets, "has", lambda *_: pytest.fail("GET consulted secrets"))
    assert ops.lookup(operation).stale is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_revision", 0),
        ("state", "other"),
        ("input_stamp", "invalid"),
        ("result_json", '{"secret":"not a result"}'),
        ("code", "token=secret"),
    ],
)
def test_corrupt_receipt_never_becomes_not_found_or_leaks_body(env, field, value):
    store, secrets, profile, ops, _, respond = env
    operation = str(uuid4())
    asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
            profile.profile_id,
            operation_id=operation,
            expected_profile_revision=1,
        )
    )
    with sqlite3.connect(store.database_path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(f"UPDATE model_probe_operations SET {field}=?", (value,))
    with pytest.raises(ProbeOperationUnavailable, match="^model_probe_operation_unavailable$"):
        ops.lookup(operation)


def test_probe_operation_lookup_never_creates_a_missing_database(tmp_path):
    store = ModelProfileStore(tmp_path / "initial.sqlite3")
    missing = tmp_path / "missing.sqlite3"
    store.database_path = missing
    with pytest.raises(ProbeOperationUnavailable):
        ModelProbeOperations(store).lookup(str(uuid4()))
    assert not missing.exists()


def test_later_probe_same_profile_revision_marks_historical_result_stale(env):
    store, secrets, profile, ops, calls, respond = env

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond))
        first_id, next_id = str(uuid4()), str(uuid4())
        first = await prober.probe(
            profile.profile_id, operation_id=first_id, expected_profile_revision=1
        )
        assert first.result is not None and first.result.profile_revision == 2
        second = await prober.probe(
            profile.profile_id, operation_id=next_id, expected_profile_revision=2
        )
        assert second.result is not None and second.result.profile_revision == 2
        assert second.stale is False
        earlier = ops.lookup(first_id)
        assert earlier.result == first.result and earlier.stale is True
        assert len(calls) == 6

    asyncio.run(scenario())


def test_missing_key_is_a_completed_negative_receipt_without_transport(env):
    store, secrets, profile, ops, calls, respond = env
    changed = store.update(
        profile.profile_id, ModelProfilePatch(expected_revision=1, clear_api_key=True), secrets
    )
    operation = str(uuid4())
    result = asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
            profile.profile_id,
            operation_id=operation,
            expected_profile_revision=changed.revision,
        )
    )
    assert result.state == "completed" and result.result is not None
    assert result.result.stages[0].code == "missing_api_key"
    assert not result.result.capabilities.model_available
    assert ops.lookup(operation) == result and calls == []


def test_legacy_probe_started_first_cannot_publish_over_pending_operation(env):
    store, secrets, profile, ops, calls, respond = env

    async def scenario():
        legacy_entered, legacy_release = asyncio.Event(), asyncio.Event()
        new_entered, new_release = asyncio.Event(), asyncio.Event()

        async def legacy_transport(request):
            legacy_entered.set()
            await legacy_release.wait()
            return respond(request)

        async def new_transport(request):
            new_entered.set()
            await new_release.wait()
            return respond(request)

        legacy = asyncio.create_task(
            ModelProber(
                store=store, secrets=secrets, transport=httpx.MockTransport(legacy_transport)
            ).probe(profile.profile_id)
        )
        await asyncio.wait_for(legacy_entered.wait(), 3)
        operation = str(uuid4())
        identified = asyncio.create_task(
            ModelProber(
                store=store, secrets=secrets, transport=httpx.MockTransport(new_transport)
            ).probe(profile.profile_id, operation_id=operation, expected_profile_revision=1)
        )
        try:
            await asyncio.wait_for(new_entered.wait(), 3)
            legacy_release.set()
            with pytest.raises(ProbeOperationConflict):
                await legacy
            assert ops.lookup(operation).state == "pending"
            assert store.require(profile.profile_id, secrets).revision == 1
        finally:
            legacy_release.set()
            new_release.set()
            await asyncio.gather(legacy, identified, return_exceptions=True)
        assert identified.result().state == "completed"

    asyncio.run(scenario())


def test_timeout_receipt_stays_unknown_and_legacy_cannot_bypass_it(env):
    store, secrets, profile, ops, calls, _ = env
    operation = str(uuid4())

    def timeout(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("private upstream body", request=request)

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(timeout))
        with pytest.raises(ProbeOperationUnavailable, match="^model_probe_operation_unavailable$"):
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=1
            )
        assert ops.lookup(operation).state == "pending"
        with pytest.raises(ProbeOperationConflict):
            await prober.probe(profile.profile_id)
        assert len(calls) == 1

    asyncio.run(scenario())


def test_reservation_failure_never_reaches_transport(env):
    store, secrets, profile, _, calls, respond = env
    with sqlite3.connect(store.database_path) as db:
        db.execute("CREATE TABLE model_probe_operations (wrong TEXT)")
    with pytest.raises(ProbeOperationUnavailable):
        asyncio.run(
            ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
                profile.profile_id,
                operation_id=str(uuid4()),
                expected_profile_revision=1,
            )
        )
    assert calls == []
    with pytest.raises(ProbeOperationUnavailable):
        ModelProbeOperations(store).lookup(str(uuid4()))


@pytest.mark.parametrize(
    "operation,revision",
    [
        ("not-uuid", 1),
        ("123e4567-e89b-12d3-a456-426614174000", 0),
        ("123e4567-e89b-12d3-a456-426614174001", True),
    ],
)
def test_invalid_operation_input_never_reserves_or_sends(env, operation, revision):
    store, secrets, profile, _, calls, respond = env
    with pytest.raises(ValueError):
        asyncio.run(
            ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
                profile.profile_id,
                operation_id=operation,
                expected_profile_revision=revision,
            )
        )
    assert calls == []
    with closing(sqlite3.connect(store.database_path)) as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='model_probe_operations'").fetchone()
            is None
        )


def test_expired_pending_reservation_reads_back_as_failed_and_unfences(env):
    """2026-10-12: a stuck pending probe locked the onboarding form forever.

    A reservation older than the TTL must read back as an explicit failure,
    unblock new probes for the profile, and heal rows written before the
    `reserved_at` column existed (they are NULL and expire immediately).
    """
    store, secrets, profile, ops, calls, respond = env

    def timeout(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("private upstream body", request=request)

    operation = str(uuid4())
    with pytest.raises(ProbeOperationUnavailable):
        asyncio.run(
            ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(timeout)).probe(
                profile.profile_id,
                operation_id=operation,
                expected_profile_revision=profile.revision,
            )
        )

    with closing(sqlite3.connect(store.database_path)) as db:
        db.execute(
            "UPDATE model_probe_operations SET reserved_at=? WHERE operation_id=?",
            (("2000-01-01T00:00:00+00:00"), operation),
        )
        db.commit()

    receipt = ops.lookup(operation)
    assert receipt.state == "failed"
    assert receipt.code == "model_probe_execution_failed"
    # A legacy (unidentified) probe is allowed again: the fence is over.
    result = asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond)).probe(
            profile.profile_id
        )
    )
    assert result.profile_id == profile.profile_id


def test_pre_reserved_at_row_is_treated_as_expired(env):
    """Rows written before the column exists are exactly the stuck ones."""
    store, secrets, profile, ops, calls, _ = env

    def timeout(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("private upstream body", request=request)

    operation = str(uuid4())
    with pytest.raises(ProbeOperationUnavailable):
        asyncio.run(
            ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(timeout)).probe(
                profile.profile_id,
                operation_id=operation,
                expected_profile_revision=profile.revision,
            )
        )
    with closing(sqlite3.connect(store.database_path)) as db:
        db.execute(
            "UPDATE model_probe_operations SET reserved_at=NULL WHERE operation_id=?",
            (operation,),
        )
        db.commit()
    receipt = ops.lookup(operation)
    assert receipt.state == "failed"
    assert receipt.code == "model_probe_execution_failed"


def test_fresh_pending_reservation_still_fences(env):
    """The fence itself is unchanged: a young pending op blocks everything."""
    store, secrets, profile, ops, calls, _ = env
    operation = str(uuid4())

    def timeout(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("private upstream body", request=request)

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(timeout))
        with pytest.raises(ProbeOperationUnavailable):
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=1
            )
        # Fresh reservation: still pending, still fenced.
        assert ops.lookup(operation).state == "pending"
        with pytest.raises(ProbeOperationConflict):
            await prober.probe(profile.profile_id)

    asyncio.run(scenario())


def test_terminal_identity_conflict_and_postcommit_reply_loss(env, monkeypatch):
    store, secrets, profile, ops, calls, respond = env
    operation = str(uuid4())
    original_lookup = ModelProbeOperations.lookup

    def lost_lookup(self, identifier):
        raise ProbeOperationUnavailable("model_probe_operation_unavailable")

    async def scenario():
        prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(respond))
        with monkeypatch.context() as patch:
            patch.setattr(ModelProbeOperations, "lookup", lost_lookup)
            with pytest.raises(ProbeOperationUnavailable):
                await prober.probe(
                    profile.profile_id, operation_id=operation, expected_profile_revision=1
                )
        receipt = original_lookup(ops, operation)
        assert receipt.state == "completed"
        assert (
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=1
            )
            == receipt
        )
        with pytest.raises(ProbeOperationConflict):
            await prober.probe(
                profile.profile_id, operation_id=operation, expected_profile_revision=2
            )
        assert len(calls) == 3

    asyncio.run(scenario())


def test_probe_api_optional_body_lookup_and_authentication(tmp_path, monkeypatch):
    from one_memory.api import create_app
    from one_memory.config import Settings

    def forbidden(*args, **kwargs):
        pytest.fail("Real network forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)

    async def scenario():
        secrets = InMemorySecretStore()
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token="synthetic-token",
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
                live_sync_watch_enabled=False,
            ),
            source_catalog={},
            secret_store=secrets,
        )
        async with app.router.lifespan_context(app):
            calls = []

            def transport(request):
                calls.append(request.url.path)
                if request.url.path.endswith("/models"):
                    return httpx.Response(200, json={"data": [{"id": "fixture"}]})
                if request.url.path.endswith("/embeddings"):
                    return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
                return httpx.Response(
                    200, json={"choices": [{"message": {"content": '{"memory_probe":true}'}}]}
                )

            app.state.model_prober = ModelProber(
                store=app.state.model_profile_store,
                secrets=secrets,
                transport=httpx.MockTransport(transport),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                operation = str(uuid4())
                url = "/api/v1/model-probe-operations/" + operation
                assert (await client.get(url)).status_code == 401
                assert (
                    await client.post(
                        "/api/v1/model-profiles/missing/probe",
                        json={
                            "operation_id": operation,
                            "expected_profile_revision": 1,
                        },
                    )
                ).status_code == 401
                client.headers["Authorization"] = "Bearer synthetic-token"
                assert (
                    await client.get("/api/v1/model-probe-operations/not-uuid")
                ).status_code == 422
                assert (await client.get(url)).json()["state"] == "not_found"
                created = await client.post(
                    "/api/v1/model-profiles",
                    json={
                        "name": "fixture",
                        "provider_kind": "openai_compatible",
                        "model": "fixture",
                        "base_url": "https://fixture.invalid/v1",
                        "api_key": "synthetic-key",
                        "embedding_provider_kind": "openai_compatible",
                        "embedding_model": "fixture-vector",
                        "embedding_base_url": "https://fixture.invalid/v1",
                        "embedding_dimensions": 2,
                    },
                )
                assert created.status_code == 200
                endpoint = f"/api/v1/model-profiles/{created.json()['profile_id']}/probe"
                for body in (
                    {},
                    {"operation_id": operation},
                    {"operation_id": operation, "expected_profile_revision": 1, "unexpected": True},
                ):
                    denied = await client.post(endpoint, json=body)
                    assert denied.status_code == 422
                assert calls == []
                legacy = await client.post(endpoint)
                assert legacy.status_code == 200 and "state" not in legacy.json()
                with closing(sqlite3.connect(app.state.model_profile_store.database_path)) as db:
                    assert (
                        db.execute(
                            "SELECT 1 FROM sqlite_master WHERE name='model_probe_operations'"
                        ).fetchone()
                        is None
                    )
                payload = {"operation_id": operation, "expected_profile_revision": 1}
                result = await client.post(endpoint, json=payload)
                assert result.status_code == 200, result.text
                assert result.json()["state"] == "completed"
                assert "synthetic-key" not in result.text and "fixture.invalid" not in result.text
                assert (await client.get(url)).json() == result.json()
                assert (await client.post(endpoint, json=payload)).json() == result.json()
                assert len(calls) == 6
                assert app.state.model_profile_store.active_identity() is None
                assert app.state.memory_task_store.list() == []

                timeout_id = str(uuid4())

                def timeout(request):
                    calls.append(request.url.path)
                    raise httpx.ReadTimeout("private network error", request=request)

                app.state.model_prober = ModelProber(
                    store=app.state.model_profile_store,
                    secrets=secrets,
                    transport=httpx.MockTransport(timeout),
                )
                unknown = await client.post(
                    endpoint,
                    json={
                        "operation_id": timeout_id,
                        "expected_profile_revision": 1,
                    },
                )
                assert unknown.status_code == 503
                assert unknown.json() == {"detail": "model_probe_operation_unavailable"}
                assert (await client.get("/api/v1/model-probe-operations/" + timeout_id)).json()[
                    "state"
                ] == "pending"
                conflict = await client.post(
                    endpoint,
                    json={
                        "operation_id": timeout_id,
                        "expected_profile_revision": 1,
                    },
                )
                assert conflict.status_code == 409
                assert conflict.json() == {"detail": "model_probe_operation_pending"}
                assert len(calls) == 7

    asyncio.run(scenario())
