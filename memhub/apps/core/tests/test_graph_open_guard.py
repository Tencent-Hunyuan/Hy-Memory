"""The ultra graph's guarded first open: marker, quarantine and rebuild.

Field failure: Kuzu's open of a bloated graph held the GIL, grew RSS to 13 GB
and the OS killed Core; the Runtime relaunched it into the same file forever.
`graph_open_guard` opens the graph before readiness behind a durable marker,
and the start after two failed opens renames the graph aside and rebuilds it.
The hang and the kill are simulated with fake opens (a blocking one, one that
exits the process); nothing here allocates real memory.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api as api_module
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.graph_open_guard import (
    KEEP_QUARANTINED,
    MARKER_NAME,
    QUARANTINE_LEDGER_NAME,
    STARTUP_RECEIPT_NAME,
    GraphOpenGuard,
)
from one_memory.hy_graph_admin import live_graph_path
from one_memory.memory_server.config_state import ConfigStore
from one_memory.sdk_write_processor import SdkWriteCoordinator
from one_memory.secrets import InMemorySecretStore

TOKEN = "graph-open-guard-test"


def _graph(tmp_path: Path) -> Path:
    graph = tmp_path / "hy-memory" / "governed-v2" / "graph"
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_bytes(b"kuzu" * 1024)
    graph.with_name("graph.wal").write_bytes(b"wal")
    return graph


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _guard(tmp_path: Path, open_graph=lambda _path: None, **kwargs) -> GraphOpenGuard:
    return GraphOpenGuard(tmp_path, open_graph=open_graph, now=_Clock(), **kwargs)


def _marker(tmp_path: Path) -> dict[str, object] | None:
    path = tmp_path / MARKER_NAME
    return json.loads(path.read_text()) if path.exists() else None


# --- the marker lifecycle -------------------------------------------------------


def test_the_marker_is_written_before_the_open_and_cleared_after_it(tmp_path) -> None:
    graph = _graph(tmp_path)
    seen: dict[str, object] = {}

    def open_graph(path: Path) -> None:
        seen["marker"] = _marker(tmp_path)
        seen["receipt"] = json.loads((tmp_path / STARTUP_RECEIPT_NAME).read_text())

    guard = _guard(tmp_path, open_graph, instance_id="core-1", budget_ms=1234)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"

    marker = seen["marker"]
    assert isinstance(marker, dict)
    assert marker["attempts"] == 1 and marker["graph_path"] == str(graph)
    receipt = seen["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["phase"] == "graph_open"
    assert receipt["instance_id"] == "core-1" and receipt["budget_ms"] == 1234
    # A successful open leaves nothing behind.
    assert _marker(tmp_path) is None
    assert not (tmp_path / STARTUP_RECEIPT_NAME).exists()
    assert guard.status().state == "ready"
    assert guard.status().failed_open_attempts == 0


def test_a_start_that_died_in_the_open_is_counted_by_the_next(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 1, "graph_path": str(graph), "started_at": "x"})
    )
    seen: list[object] = []
    guard = _guard(tmp_path, lambda _path: seen.append(_marker(tmp_path)))
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"
    # Second attempt in a row: the marker says so while it runs.
    assert seen and isinstance(seen[0], dict) and seen[0]["attempts"] == 2
    assert _marker(tmp_path) is None
    assert graph.exists()


def test_a_missing_graph_clears_a_stale_marker_and_opens_nothing(tmp_path) -> None:
    graph = tmp_path / "hy-memory" / "governed-v2" / "graph"
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 5, "graph_path": str(graph), "started_at": "x"})
    )
    calls: list[Path] = []
    guard = _guard(tmp_path, calls.append)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "absent"
    assert calls == [] and _marker(tmp_path) is None


def test_failures_on_another_graph_are_not_counted(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 9, "graph_path": str(graph) + "-old", "started_at": "x"})
    )
    guard = _guard(tmp_path)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"
    assert graph.exists()


# --- two failed opens: quarantine ------------------------------------------------


def test_two_failed_starts_quarantine_the_graph_by_renaming_it(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    calls: list[Path] = []
    guard = _guard(tmp_path, calls.append)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    # Not opened again: that is what took the last two starts down.
    assert calls == []
    assert not graph.exists() and not graph.with_name("graph.wal").exists()
    renamed = sorted(p.name for p in graph.parent.iterdir())
    assert len(renamed) == 2
    assert all(name.startswith("graph.quarantined-") for name in renamed)
    stem = next(name for name in renamed if not name.endswith(".wal"))
    assert (graph.parent / stem).read_bytes() == b"kuzu" * 1024
    assert (graph.parent / f"{stem}.wal").read_bytes() == b"wal"
    assert _marker(tmp_path) is None
    status = guard.status()
    assert status.state == "rebuilding_after_quarantine"
    assert status.quarantine_count == 1 and status.last_quarantined_at is not None
    ledger = json.loads((tmp_path / QUARANTINE_LEDGER_NAME).read_text())
    assert ledger["rebuild_pending"] is True


def test_an_open_that_raises_twice_quarantines_in_the_same_start(tmp_path) -> None:
    graph = _graph(tmp_path)
    calls: list[Path] = []

    def broken(path: Path) -> None:
        calls.append(path)
        raise RuntimeError("Kuzu: corrupted database")

    guard = _guard(tmp_path, broken)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert len(calls) == 2
    assert not graph.exists()
    assert guard.status().quarantine_count == 1


def test_only_the_newest_quarantined_copies_are_kept(tmp_path) -> None:
    clock = _Clock()
    for _ in range(KEEP_QUARANTINED + 2):
        graph = _graph(tmp_path)
        GraphOpenGuard(tmp_path, open_graph=lambda _p: None, now=clock).quarantine(
            graph, failed_attempts=2
        )
    kept = sorted(p.name for p in graph.parent.iterdir() if not p.name.endswith(".wal"))
    assert len(kept) == KEEP_QUARANTINED
    assert sorted(p.name for p in graph.parent.iterdir() if p.name.endswith(".wal")) == [
        f"{name}.wal" for name in kept
    ]
    ledger = json.loads((tmp_path / QUARANTINE_LEDGER_NAME).read_text())
    assert ledger["count"] == KEEP_QUARANTINED + 2


def test_the_rebuild_state_settles_once_the_rebuild_is_done(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    guard = _guard(tmp_path)
    guard.open_at_startup(graph, switch_in_progress=False)
    assert guard.status(rebuild_running=True).state == "rebuilding_after_quarantine"
    assert guard.status(rebuild_running=None).state == "rebuilding_after_quarantine"
    settled = guard.status(rebuild_running=False)
    assert settled.state == "ready" and settled.quarantine_count == 1
    # A later start reports ready with the count kept.
    later = _guard(tmp_path)
    _graph(tmp_path)
    later.open_at_startup(graph, switch_in_progress=False)
    assert later.status().state == "ready" and later.status().quarantine_count == 1


# --- embedding switch ------------------------------------------------------------


def test_no_quarantine_while_an_embedding_switch_is_open(tmp_path) -> None:
    graph = _graph(tmp_path)
    staging = graph.with_name("graph-abcdef12")
    staging.write_bytes(b"staging")
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    calls: list[Path] = []
    guard = _guard(tmp_path, calls.append)
    assert guard.open_at_startup(graph, switch_in_progress=True) == "skipped"
    # Neither the live graph (the switch's source) nor the staging graph moves,
    # and the known-bad graph is not opened again.
    assert graph.exists() and staging.read_bytes() == b"staging"
    assert calls == []
    assert guard.status().state == "unavailable"
    assert guard.status().quarantine_count == 0
    # The marker stays, so the first start after the switch quarantines.
    marker = _marker(tmp_path)
    assert marker is not None and marker["attempts"] == 2
    after = _guard(tmp_path)
    assert after.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert staging.read_bytes() == b"staging"


def test_a_failed_open_during_a_switch_is_not_quarantined(tmp_path) -> None:
    graph = _graph(tmp_path)

    def broken(_path: Path) -> None:
        raise RuntimeError("busy")

    guard = _guard(tmp_path, broken)
    assert guard.open_at_startup(graph, switch_in_progress=True) == "failed"
    assert graph.exists()
    marker = _marker(tmp_path)
    assert marker is not None and marker["attempts"] == 1


# --- real process deaths and hangs ------------------------------------------------


_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from one_memory.graph_open_guard import GraphOpenGuard

    data_dir, graph, behaviour = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

    def open_graph(_path):
        if behaviour == "exit":
            # What the OS kill looks like from here: no cleanup at all.
            os._exit(137)
        if behaviour == "hang":
            while True:
                time.sleep(0.05)

    outcome = GraphOpenGuard(data_dir, open_graph=open_graph).open_at_startup(
        graph, switch_in_progress=False
    )
    print(outcome)
    """
)


def _start(tmp_path: Path, graph: Path, behaviour: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(tmp_path), str(graph), behaviour],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_starts_killed_in_the_open_lead_to_quarantine_on_the_third(tmp_path) -> None:
    graph = _graph(tmp_path)
    for attempt in (1, 2):
        child = _start(tmp_path, graph, "exit")
        assert child.wait(timeout=60) == 137
        marker = _marker(tmp_path)
        assert marker is not None and marker["attempts"] == attempt
        assert graph.exists()
    third = _start(tmp_path, graph, "open")
    out, err = third.communicate(timeout=60)
    assert third.returncode == 0, err
    assert out.strip() == "quarantined"
    assert not graph.exists()
    assert any(p.name.startswith("graph.quarantined-") for p in graph.parent.iterdir())
    assert _marker(tmp_path) is None


def test_a_start_killed_while_the_open_hangs_leaves_the_marker(tmp_path) -> None:
    graph = _graph(tmp_path)
    child = _start(tmp_path, graph, "hang")
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / STARTUP_RECEIPT_NAME).exists():
            assert time.monotonic() < deadline, "the startup phase was never declared"
            assert child.poll() is None, child.stderr.read() if child.stderr else ""
            time.sleep(0.02)
        receipt = json.loads((tmp_path / STARTUP_RECEIPT_NAME).read_text())
        assert receipt["phase"] == "graph_open" and receipt["pid"] == child.pid
    finally:
        # The supervisor at the end of the startup budget.
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=30)
    marker = _marker(tmp_path)
    assert marker is not None and marker["attempts"] == 1


# --- Core startup ----------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr(TOKEN),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


class _GraphSdk:
    def __init__(self) -> None:
        self.graph_builds: list[str] = []

    def build_memory_graph(self, user_id, **_kwargs):
        self.graph_builds.append(user_id)
        return {"success": True, "memories_scanned": 0, "nodes": 0}

    def memory_stats(self, *, user_id, statuses=None):
        return {"total": 1, "scenes": {}}


def _run_core(tmp_path, monkeypatch, *, mode: str, opens: list[Path], scenario) -> None:
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    ConfigStore(settings.data_dir).set_memory_mode(mode)  # type: ignore[arg-type]
    # The real open is Kuzu's; here it only records that it happened.
    original_init = GraphOpenGuard.__init__

    def init(self, data_dir, **kwargs):
        kwargs.setdefault("open_graph", lambda path: opens.append(path))
        original_init(self, data_dir, **kwargs)

    monkeypatch.setattr(GraphOpenGuard, "__init__", init)

    async def main() -> None:
        app = create_app(settings, secret_store=InMemorySecretStore())
        sdk = _GraphSdk()

        @asynccontextmanager
        async def write_client(_self):
            yield sdk

        # Before startup: post-ready recovery may start the rebuild at once.
        monkeypatch.setattr(SdkWriteCoordinator, "use_client", write_client)
        async with app.router.lifespan_context(app):
            if app.state.recovery_task is not None:
                await asyncio.wait_for(asyncio.shield(app.state.recovery_task), 30)
            async with _client(app) as client:
                await scenario(app, sdk, client)

    asyncio.run(main())


@pytest.mark.fresh_sdk
def test_ultra_startup_opens_the_graph_before_ready(tmp_path, monkeypatch) -> None:
    graph = live_graph_path(tmp_path / "data", "graph")
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_bytes(b"kuzu")
    opens: list[Path] = []

    async def scenario(app, _sdk, client):
        assert opens == [graph]
        assert app.state.graph_open_outcome == "opened"
        body = (await client.get("/api/v1/memory-processing")).json()
        assert body["pipeline"]["graph_store"] == {
            "state": "ready",
            "quarantine_count": 0,
            "last_quarantined_at": None,
            "failed_open_attempts": 0,
        }
        mode = (await client.get("/api/v1/settings/memory-mode")).json()
        assert mode["graph_store"]["state"] == "ready"

    _run_core(tmp_path, monkeypatch, mode="ultra", opens=opens, scenario=scenario)
    assert not (tmp_path / "data" / MARKER_NAME).exists()


@pytest.mark.fresh_sdk
def test_pro_startup_never_opens_the_graph(tmp_path, monkeypatch) -> None:
    opens: list[Path] = []

    async def scenario(app, _sdk, client):
        assert opens == []
        body = (await client.get("/api/v1/memory-processing")).json()
        assert body["pipeline"]["graph_store"]["state"] == "not_used"

    _run_core(tmp_path, monkeypatch, mode="pro", opens=opens, scenario=scenario)


@pytest.mark.fresh_sdk
def test_ultra_startup_after_two_failed_opens_quarantines_and_rebuilds(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    graph = live_graph_path(data_dir, "graph")
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_bytes(b"bloated")
    (data_dir / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    opens: list[Path] = []

    async def scenario(app, sdk, client):
        assert app.state.graph_open_outcome == "quarantined"
        assert opens == []
        await app.state.memory_graph_backfill.wait()
        # The normal per-user rebuild, from the SDK's memories.
        assert sdk.graph_builds == ["local"]
        body = (await client.get("/api/v1/memory-processing")).json()
        graph_store = body["pipeline"]["graph_store"]
        assert graph_store["quarantine_count"] == 1
        assert graph_store["last_quarantined_at"] is not None
        # The rebuild finished, so the state settles back to ready.
        assert graph_store["state"] == "ready"

    _run_core(tmp_path, monkeypatch, mode="ultra", opens=opens, scenario=scenario)
    # Whatever the SDK opened afterwards is a fresh graph, not the bloated one.
    assert not graph.is_file() or graph.read_bytes() != b"bloated"
    quarantined = [
        p for p in graph.parent.iterdir() if p.name.startswith("graph.quarantined-") and p.is_file()
    ]
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == b"bloated"
    assert not (data_dir / MARKER_NAME).exists()


def test_the_status_helper_reports_rebuilding_until_recovery_has_run(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    guard = _guard(tmp_path)
    guard.open_at_startup(graph, switch_in_progress=False)

    class _State:
        graph_open_guard = guard
        memory_graph_backfill = None
        recovery = "running"

        class evolve_state_store:  # noqa: N801 - mirrors app.state's attribute
            @staticmethod
            def graph_build():
                class _Build:
                    state = "idle"

                return _Build()

    class _App:
        state = _State()

    status = api_module._graph_store_status(_App())  # type: ignore[arg-type]
    assert status.state == "rebuilding_after_quarantine"
    _State.recovery = "done"
    assert api_module._graph_store_status(_App()).state == "ready"  # type: ignore[arg-type]
