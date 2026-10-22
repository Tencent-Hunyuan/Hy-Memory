"""No user data: connection retries must retain the original intent and job."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import source_root_identity
from one_memory.imports.connections import SourceConnectionStore
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.onboarding import OnboardingStore


@pytest.mark.parametrize("entry", ["connect", "authorize"])
def test_legacy_entry_cannot_authorize_an_unconfirmed_source_identity(tmp_path, monkeypatch, entry):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=home,
                api_token=SecretStr("test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            source_catalog=build_source_catalog(home=home, environment={}),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            endpoint = (
                "/api/v1/sources/connect"
                if entry == "connect"
                else "/api/v1/sources/claude-code/authorize"
            )
            body = {"source_ids": ["claude-code"]} if entry == "connect" else {}
            denied = await client.post(endpoint, json=body)
            assert denied.status_code == 409
            assert denied.json()["detail"] == "source_confirmation_required"
            assert (
                app.state.source_grant_store.receipt(user_id="local", source_id="claude-code")
                is None
            )
            assert app.state.import_job_store.list_jobs() == []
            root_id = (await client.post("/api/v1/sources/claude-code/location")).json()["root_id"]
            explicit = (
                {
                    "source_ids": ["claude-code"],
                    "request_id": "confirmed",
                    "source_roots": {"claude-code": root_id},
                }
                if entry == "connect"
                else {"expected_root_id": root_id, "user_confirmed": True}
            )
            allowed = await client.post(endpoint, json=explicit)
            assert allowed.status_code == 200, allowed.text
            assert (
                app.state.source_grant_store.require_receipt(
                    user_id="local", source_id="claude-code"
                ).revision
                == 1
            )

    asyncio.run(scenario())


def test_authorize_rejects_replaced_root_after_preview(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=home,
                api_token=SecretStr("test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            source_catalog=build_source_catalog(home=home, environment={}),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            root_id = (await client.post("/api/v1/sources/claude-code/location")).json()["root_id"]
            root.rename(root.with_name("former"))
            root.mkdir()
            response = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json={
                    "expected_root_id": root_id,
                    "user_confirmed": True,
                },
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "source_identity_changed"
            assert (
                app.state.source_grant_store.receipt(user_id="local", source_id="claude-code")
                is None
            )

    asyncio.run(scenario())


def test_connection_retry_reuses_completed_job_and_checks_root_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        (root / "s.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "s",
                    "uuid": "1",
                    "message": {"content": "existing"},
                }
            )
            + "\n"
        )
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=home,
                api_token=SecretStr("test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            source_catalog=build_source_catalog(home=home, environment={}),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            found = await client.post("/api/v1/sources/claude-code/location")
            assert found.status_code == 200
            request = {
                "source_ids": ["claude-code"],
                "request_id": "intent-1",
                "source_roots": {"claude-code": found.json()["root_id"]},
                "onboarding": True,
                "full_rescan": True,
                "enable_sync": True,
            }
            initial = await client.post("/api/v1/sources/connect", json=request)
            assert initial.status_code == 200
            job_id = initial.json()["jobs"][0]["job_id"]
            await app.state.import_supervisor.wait(job_id)
            grant = app.state.source_grant_store.receipt(user_id="local", source_id="claude-code")
            assert grant is not None
            assert app.state.config_store.sync_enabled("claude-code")
            job_count = len(app.state.import_job_store.list_jobs())
            retry = await client.post("/api/v1/sources/connect", json=request)
            assert retry.status_code == 200
            assert retry.json()["jobs"][0]["job_id"] == job_id
            assert retry.json()["jobs"][0]["status"] == "completed"
            assert (
                app.state.source_grant_store.receipt(user_id="local", source_id="claude-code")
                == grant
            )
            assert len(app.state.import_job_store.list_jobs()) == job_count
            request["source_roots"] = {"claude-code": "root:v2:wrong"}
            denied = await client.post("/api/v1/sources/connect", json=request)
            assert denied.status_code == 409
            assert len(app.state.import_job_store.list_jobs()) == job_count

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("turn_count", "age_days", "limited"),
    [
        (200, 4, False),
        (201, 1, False),
        (201, 4, True),
    ],
)
def test_onboarding_applies_large_history_window_to_all_sources(
    tmp_path,
    monkeypatch,
    turn_count,
    age_days,
    limited,
):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        claude = home / ".claude/projects"
        codex = home / ".codex/sessions"
        claude.mkdir(parents=True)
        codex.mkdir(parents=True)
        timestamp = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
        (claude / "large-session.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "large-session",
                        "uuid": f"user-{index}",
                        "timestamp": timestamp,
                        "message": {"content": f"turn {index}"},
                    }
                )
                + "\n"
                for index in range(turn_count)
            )
        )
        (codex / "one.jsonl").write_text("")
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=home,
                api_token=SecretStr("test"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            source_catalog=build_source_catalog(home=home, environment={}),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            claude_root = (await client.post("/api/v1/sources/claude-code/location")).json()[
                "root_id"
            ]
            codex_root = (await client.post("/api/v1/sources/codex/location")).json()["root_id"]
            before = datetime.now(UTC)
            response = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["claude-code", "codex"],
                    "request_id": f"history-window-{turn_count}-{age_days}",
                    "source_roots": {
                        "claude-code": claude_root,
                        "codex": codex_root,
                    },
                    "onboarding": True,
                    "full_rescan": True,
                },
            )
            after = datetime.now(UTC)
            assert response.status_code == 200, response.text
            cutoffs = {job["history_after"] for job in response.json()["jobs"]}
            if not limited:
                assert cutoffs == {None}
                return
            assert len(cutoffs) == 1
            cutoff = datetime.fromisoformat(cutoffs.pop())
            assert before - timedelta(days=3) <= cutoff <= after - timedelta(days=3)

    asyncio.run(scenario())


def intent_setup(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    database = tmp_path / "data.sqlite"
    grants = SourceGrantStore(database, home=home)
    jobs = ImportJobStore(database)
    OnboardingStore(database)
    store = SourceConnectionStore(grants, jobs, home=home)
    catalog = build_source_catalog(home=home, environment={})
    kwargs = dict(
        request_id="first",
        registrations=(catalog["claude-code"],),
        source_roots={"claude-code": source_root_identity(root)},
        confirm_overrides=False,
        full_rescan=False,
    )
    return store, kwargs, root, database, jobs


def test_connection_snapshot_rejects_replaced_root_without_grant(tmp_path):
    store, kwargs, root, database, jobs = intent_setup(tmp_path)
    root.rename(root.with_name("old"))
    root.mkdir()
    receipt = store.connect(**kwargs)
    assert receipt.accepted_source_ids == ()
    assert receipt.failed_sources == {"claude-code": "source_identity_changed"}
    assert jobs.list_jobs() == []
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM source_grants").fetchone()[0] == 0


def test_connection_grant_and_job_roll_back_when_receipt_cannot_commit(tmp_path):
    store, kwargs, root, database, jobs = intent_setup(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TRIGGER deny_receipt BEFORE INSERT ON source_connection_intents "
            "BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.connect(**kwargs)
    assert jobs.list_jobs() == []
    assert store.grants.receipt(user_id="local", source_id="claude-code") is None
    with sqlite3.connect(database) as db:
        db.execute("DROP TRIGGER deny_receipt")
    first = store.connect(**kwargs)
    second = SourceConnectionStore(store.grants, jobs, home=root.parents[1]).get("first")
    assert first == second


def test_concurrent_same_intent_creates_exactly_one_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store, kwargs, root, _, jobs = intent_setup(tmp_path)
    other = SourceConnectionStore(store.grants, jobs, home=root.parents[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(target.connect, **kwargs) for target in (store, other)]
        first, second = [future.result(timeout=10) for future in futures]
    assert first == second
    assert len(jobs.list_jobs()) == 1


def test_partial_connection_has_only_accepted_jobs_and_no_regrant_on_retry(tmp_path):
    store, kwargs, root, _, jobs = intent_setup(tmp_path)
    missing = build_source_catalog(home=root.parents[1], environment={})["workbuddy"]
    kwargs["registrations"] += (missing,)
    kwargs["source_roots"]["workbuddy"] = "root:v2:missing"
    receipt = store.connect(**kwargs)
    assert receipt.accepted_source_ids == ("claude-code",)
    assert "workbuddy" in receipt.failed_sources
    assert len(jobs.list_jobs()) == 1
    before = store.grants.receipt(user_id="local", source_id="claude-code")
    assert store.connect(**kwargs) == receipt
    assert store.grants.receipt(user_id="local", source_id="claude-code") == before


def test_onboarding_persists_one_history_window_on_every_accepted_source(tmp_path):
    home = tmp_path / "home"
    claude = home / ".claude/projects"
    codex = home / ".codex/sessions"
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)
    database = tmp_path / "data.sqlite"
    grants = SourceGrantStore(database, home=home)
    jobs = ImportJobStore(database)
    OnboardingStore(database)
    store = SourceConnectionStore(grants, jobs, home=home)
    catalog = build_source_catalog(home=home, environment={})
    cutoff = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
    receipt = store.connect(
        request_id="bounded-first-import",
        registrations=(catalog["claude-code"], catalog["codex"]),
        source_roots={
            "claude-code": source_root_identity(claude),
            "codex": source_root_identity(codex),
        },
        confirm_overrides=False,
        full_rescan=True,
        onboarding=True,
        history_after=cutoff,
    )

    assert receipt.accepted_source_ids == ("claude-code", "codex")
    assert {job.history_after for job in receipt.jobs} == {cutoff}
    assert store.get("bounded-first-import") == receipt
