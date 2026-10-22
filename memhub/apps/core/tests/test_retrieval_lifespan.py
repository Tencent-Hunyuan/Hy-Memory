import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.secrets import InMemorySecretStore


def test_retrieval_defaults_off_and_does_not_create_index(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        )
        assert settings.retrieval_index_enabled is False
        app = create_app(settings, secret_store=InMemorySecretStore())
        async with app.router.lifespan_context(app):
            assert app.state.retrieval_runtime is None
            with closing(sqlite3.connect(tmp_path / "one-memory.sqlite3")) as db:
                assert (
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name LIKE 'retrieval_%'"
                    ).fetchall()
                    == []
                )

    asyncio.run(scenario())


def test_enabling_retrieval_requires_runtime_ownership(tmp_path):
    with pytest.raises(ValueError, match="retrieval_runtime_ownership_required"):
        create_app(
            Settings(_env_file=None, data_dir=tmp_path, retrieval_index_enabled=True),
            secret_store=InMemorySecretStore(),
        )


def test_index_enabled_serve_imports_without_building_an_unmanaged_app(tmp_path, monkeypatch):
    """The real serve entry must not be killed by an import-time default app.

    `cli.serve()` imports `create_app` and then builds the app with
    `manage_runtime=True`. If this module also builds a default app at import
    time, enabling the index makes that throw before `serve()` ever binds.
    Run the public entry in a subprocess, not through `create_app()` directly,
    because the regression exists only in the import/entry-point combination.
    """
    monkeypatch.setenv("ONE_MEMORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ONE_MEMORY_RETRIEVAL_INDEX_ENABLED", "1")
    monkeypatch.setenv("ONE_MEMORY_RETRIEVAL_MINIMUM_FREE_BYTES", "0")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("ONE_MEMORY_HOST", "127.0.0.1")
    monkeypatch.setenv("ONE_MEMORY_PORT", str(port))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ONE_MEMORY_SOURCE_HOME", str(tmp_path / "source-home"))
    monkeypatch.setenv("ONE_MEMORY_IMPORTS_ENABLED", "0")
    monkeypatch.setenv("ONE_MEMORY_LIVE_SYNC_ENABLED", "0")
    monkeypatch.setenv("ONE_MEMORY_AUTOMATIC_MEMORY_PROCESSING", "0")

    process = subprocess.Popen(
        [sys.executable, "-m", "one_memory.cli", "serve"],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        deadline = time.monotonic() + 15
        health = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/health", timeout=0.5
                ) as response:
                    health = json.loads(response.read())
                break
            except OSError:
                time.sleep(0.1)
        if health is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            pytest.fail(
                "serve did not become healthy within 15s; "
                f"exit={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        assert health["status"] == "ok"
        assert health["service"] == "one-memory-core"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def managed(tmp_path):
    return create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
            retrieval_index_enabled=True,
        ),
        manage_runtime=True,
        secret_store=InMemorySecretStore(),
    )


def test_managed_start_without_an_encoder_preserves_normal_core_startup(tmp_path, monkeypatch):
    """No built-in weights is the real reason an index cannot be admitted.

    Core must still come up and the lexical lane must still answer; the runtime
    says not_initialized rather than taking the process down with it.
    """
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    monkeypatch.setattr(
        "one_memory.retrieval.index_provisioning.assets_present", lambda *_args: False
    )

    async def scenario():
        app = managed(tmp_path)
        async with app.router.lifespan_context(app):
            assert app.state.retrieval_runtime.snapshot().state == "not_initialized"
            assert (tmp_path / "runtime.json").exists()
            assert not (tmp_path / "retrieval-vectors").exists()
        assert not (tmp_path / "runtime.json").exists()

    asyncio.run(scenario())


def test_enabling_retrieval_provisions_the_index_instead_of_waiting_to_be_handed_one(
    tmp_path, monkeypatch
):
    """The product entry point, which until now only a test helper could reach."""
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)

    async def scenario():
        app = managed(tmp_path)
        async with app.router.lifespan_context(app):
            assert app.state.retrieval_runtime.snapshot().state == "running"
            assert (tmp_path / "retrieval-vectors").is_dir()
            with closing(sqlite3.connect(tmp_path / "one-memory.sqlite3")) as db:
                assert [
                    row[0]
                    for row in db.execute(
                        "SELECT state FROM retrieval_index_generations ORDER BY rowid"
                    )
                ] == ["active"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_retrieval_stop_failure_still_drains_other_owned_services(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    events = []

    class StubRuntime:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            events.append("start")

        async def stop(self):
            events.append("retrieval_drained")
            raise failure("after drain")

    monkeypatch.setattr("one_memory.retrieval.runtime.ExistingRetrievalRuntime", StubRuntime)

    async def scenario():
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path,
                source_home=tmp_path / "home",
                imports_enabled=False,
                automatic_memory_processing=False,
                live_sync_enabled=False,
                retrieval_index_enabled=True,
            ),
            manage_runtime=True,
            secret_store=InMemorySecretStore(),
        )
        with pytest.raises(failure):
            async with app.router.lifespan_context(app):
                original = app.state.sdk_memory_runtime.shutdown

                async def shutdown():
                    await original()
                    events.append("processing_drained")

                monkeypatch.setattr(app.state.sdk_memory_runtime, "shutdown", shutdown)
        assert events == ["start", "retrieval_drained", "processing_drained"]
        assert not (tmp_path / "runtime.json").exists()

    asyncio.run(scenario())
