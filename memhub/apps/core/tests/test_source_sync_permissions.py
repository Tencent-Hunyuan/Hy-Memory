"""S1.3 source permission tests use temporary files only, never real host history."""

import asyncio
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import SourceAccessError
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.sources.registry import RootCandidateKind, build_source_catalog
from tests.source_consent import connection_request


def grant_store(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    store = SourceGrantStore(tmp_path / "data.sqlite", home=home)
    receipt = store.grant(
        user_id="local",
        source_id="claude-code",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    return store, root, receipt


def test_replaced_root_cannot_reuse_a_grant_or_cached_file_authority(tmp_path):
    store, root, _ = grant_store(tmp_path)
    (root / "session.jsonl").write_text("original")
    authorized = store.authorized_root(user_id="local", source_id="claude-code")
    assert authorized is not None
    saved_file = authorized.authorize_file(Path("session.jsonl"))
    root.rename(root.with_name("old-projects"))
    root.mkdir()
    (root / "session.jsonl").write_text("replacement")
    with pytest.raises(SourceAccessError, match="source_identity_changed"):
        store.authorized_root(user_id="local", source_id="claude-code")
    with pytest.raises(SourceAccessError, match="source_identity_changed"):
        authorized.authorize_file(Path("session.jsonl"))
    with pytest.raises(SourceAccessError):
        saved_file.open_binary()


def test_retargeted_root_symlink_requires_new_consent(tmp_path):
    home = tmp_path / "home"
    first, second = home / "first", home / "second"
    first.mkdir(parents=True)
    second.mkdir()
    link = home / "chosen"
    link.symlink_to(first, target_is_directory=True)
    store = SourceGrantStore(tmp_path / "data.sqlite", home=home)
    store.grant(
        user_id="local",
        source_id="fixture",
        root=link,
        candidate_kind=RootCandidateKind.ENVIRONMENT_OVERRIDE,
    )
    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    with pytest.raises(SourceAccessError, match="source_identity_changed"):
        store.authorized_root(user_id="local", source_id="fixture")


def test_location_check_does_not_open_any_conversation_file(tmp_path, monkeypatch):
    from one_memory.imports.locations import inspect_source_location

    root = tmp_path / "history"
    root.mkdir()
    secret = root / "never-open.jsonl"
    secret.write_text("private content")
    real_open = Path.open
    opened = []

    def record_open(self, *args, **kwargs):
        opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", record_open)
    assert secret.read_text() == "private content"
    assert opened == [secret]
    opened.clear()
    found = inspect_source_location("fixture", root)
    assert found.state == "found" and found.root_id.startswith("root:v2:")
    assert opened == []
    assert inspect_source_location("fixture", root / "absent").state == "not_found"
    assert inspect_source_location("fixture", secret).state == "invalid"
    root.chmod(0)
    try:
        assert inspect_source_location("fixture", root).state == "inaccessible"
    finally:
        root.chmod(0o700)


def test_source_revocation_prevents_new_read_authority(tmp_path):
    store, root, _ = grant_store(tmp_path)
    (root / "session.jsonl").write_text("original")
    authorized = store.authorized_root(user_id="local", source_id="claude-code")
    source_file = authorized.authorize_file(Path("session.jsonl"))
    with source_file.open_binary() as stream:
        assert stream.read() == b"original"
    store.revoke(user_id="local", source_id="claude-code")
    assert store.authorized_root(user_id="local", source_id="claude-code") is None


def test_frozen_pending_task_stays_pending_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        write_turn(root, "not-to-be-read")
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
        )
        catalog = build_source_catalog(home=home, environment={})
        app = create_app(settings, source_catalog=catalog)
        async with app.router.lifespan_context(app):
            store = app.state.source_grant_store
            store.grant(
                user_id="local",
                source_id="claude-code",
                root=root,
                candidate_kind=RootCandidateKind.DEFAULT,
            )
            app.state.config_store.set_sync("claude-code", True)
            job = app.state.import_supervisor.create_job("claude-code", operation="sync")
            assert job.operation == "sync"
            app.state.config_store.set_sync("claude-code", False)
        restored = create_app(settings, source_catalog=catalog)
        async with restored.router.lifespan_context(restored):
            await asyncio.sleep(0.1)
            assert restored.state.evidence_store.count() == 0
            assert restored.state.import_job_store.require(job.job_id).status == "pending"

    asyncio.run(scenario())


def write_turn(root, identifier):
    (root / f"{identifier}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": identifier,
                "uuid": identifier,
                "message": {"content": identifier},
            }
        )
        + "\n"
    )


def test_one_processing_switch_starts_history_and_future_scans_then_freezes(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        write_turn(root, "initial")
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_interval_seconds=3600,
            live_sync_watch_debounce_seconds=0.05,
            live_sync_watch_min_interval_seconds=1,
        )
        app = create_app(settings, source_catalog=build_source_catalog(home=home, environment={}))
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            connected = await client.post(
                "/api/v1/sources/connect",
                json=await connection_request(
                    client,
                    ["claude-code"],
                    enable_sync=True,
                ),
            )
            assert connected.status_code == 200
            for job in connected.json()["jobs"]:
                await app.state.import_supervisor.wait(job["job_id"])
            assert app.state.evidence_store.count() == 1
            assert app.state.live_sync_watch is not None
            write_turn(root, "after-thaw")
            synced = await app.state.live_sync_supervisor.trigger("claude-code")
            assert synced is not None
            assert app.state.evidence_store.count() == 2
            grant = app.state.source_grant_store.require_receipt(
                user_id="local", source_id="claude-code"
            )
            assert grant.revoked_at is None
            stopped = await client.post(
                "/api/v1/sources/claude-code/sync-permission", json={"enabled": False}
            )
            assert stopped.status_code == 200 and not stopped.json()["sync_enabled"]
            assert app.state.live_sync_watch is None
            write_turn(root, "after-sync-disabled")
            assert await app.state.live_sync_supervisor.trigger("claude-code") is None
            assert app.state.evidence_store.count() == 2
            assert app.state.source_grant_store.authorized_root(
                user_id="local", source_id="claude-code"
            )
            assert app.state.memory_task_store.list() == []

    asyncio.run(scenario())
