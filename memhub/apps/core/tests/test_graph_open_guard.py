"""The ultra graph's guarded first open: a bounded child probe, then quarantine.

Field failure: Kuzu's open of a bloated graph held the GIL, grew RSS to
5-13 GB and the OS killed the process; the Runtime relaunched Core into the
same file forever. `graph_open_guard` opens the graph first in a short-lived
child bounded by time and RSS. A probe that is killed, crashes or exits
non-zero quarantines the graph (renamed, never deleted) on the first failure,
and the per-user rebuild runs. A durable marker still covers Core itself
dying mid-probe. Fake probes sleep, allocate past a small cap or exit; nothing
here allocates more than a few hundred MB.
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
    GraphOpenProbe,
    ProbeResult,
    probe_max_rss_bytes,
    probe_timeout_s,
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


class _StubProbe:
    """An in-process stand-in that records what the guard left on disk."""

    def __init__(self, tmp_path: Path, outcome: str = "ok", budget: float = 1.0) -> None:
        self.tmp_path = tmp_path
        self.outcome = outcome
        self.budget = budget
        self.calls: list[Path] = []
        self.seen_marker: object = None
        self.seen_receipt: object = None

    def budget_s(self, _graph_path: Path) -> float:
        return self.budget

    def __call__(self, graph_path: Path) -> ProbeResult:
        self.calls.append(graph_path)
        self.seen_marker = _marker(self.tmp_path)
        receipt = self.tmp_path / STARTUP_RECEIPT_NAME
        self.seen_receipt = json.loads(receipt.read_text()) if receipt.exists() else None
        return ProbeResult(self.outcome)  # type: ignore[arg-type]


def _guard(tmp_path: Path, probe=None, **kwargs) -> GraphOpenGuard:
    return GraphOpenGuard(tmp_path, probe=probe or _StubProbe(tmp_path), now=_Clock(), **kwargs)


def _marker(tmp_path: Path) -> dict[str, object] | None:
    path = tmp_path / MARKER_NAME
    return json.loads(path.read_text()) if path.exists() else None


def _quarantined(graph: Path) -> list[Path]:
    return [
        p
        for p in graph.parent.iterdir()
        if p.name.startswith("graph.quarantined-") and not p.name.endswith(".wal")
    ]


def _python(source: str) -> object:
    """A probe command running `source` in a child interpreter."""

    def command(_graph: Path, _alarm_s: int) -> list[str]:
        return [sys.executable, "-c", textwrap.dedent(source)]

    return command


# --- the child probe ---------------------------------------------------------------


def test_a_probe_that_sleeps_past_its_budget_is_killed() -> None:
    probe = GraphOpenProbe(command=_python("import time; time.sleep(60)"), timeout_s=0.5)  # type: ignore[arg-type]
    started = time.monotonic()
    result = probe(Path("/nonexistent"))
    assert result.outcome == "timeout" and result.condemns_graph
    assert time.monotonic() - started < 15


def test_a_probe_that_allocates_past_the_cap_is_killed() -> None:
    # 50 MB cap; the child touches 300 MB and would then sit there.
    probe = GraphOpenProbe(
        command=_python(  # type: ignore[arg-type]
            """
            import time
            block = bytearray(300 * 1024 * 1024)
            for i in range(0, len(block), 4096):
                block[i] = 1
            time.sleep(60)
            """
        ),
        timeout_s=30,
        max_rss_bytes=50 * 1024 * 1024,
        poll_interval_s=0.02,
    )
    result = probe(Path("/nonexistent"))
    assert result.outcome == "rss_cap" and result.condemns_graph
    assert result.peak_rss_bytes is not None and result.peak_rss_bytes > 50 * 1024 * 1024


def test_a_probe_that_exits_non_zero_or_crashes_condemns_the_graph() -> None:
    nonzero = GraphOpenProbe(command=_python("raise SystemExit(1)"), timeout_s=30)  # type: ignore[arg-type]
    assert nonzero(Path("/x")).outcome == "exit_nonzero"
    crashed = GraphOpenProbe(
        command=_python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),  # type: ignore[arg-type]
        timeout_s=30,
    )
    assert crashed(Path("/x")).outcome == "crashed"


def test_a_probe_that_cannot_probe_does_not_condemn_the_graph() -> None:
    unavailable = GraphOpenProbe(command=_python("raise SystemExit(3)"), timeout_s=30)  # type: ignore[arg-type]
    result = unavailable(Path("/x"))
    assert result.outcome == "unavailable" and not result.condemns_graph
    missing = GraphOpenProbe(command=lambda _g, _a: ["/nonexistent/one-memory"], timeout_s=5)
    assert missing(Path("/x")).outcome == "unavailable"


def test_the_real_probe_opens_a_real_graph_and_rejects_garbage(tmp_path) -> None:
    kuzu = pytest.importorskip("kuzu")
    graph = tmp_path / "graph"
    kuzu.Database(str(graph)).close()
    assert GraphOpenProbe(timeout_s=60)(graph).outcome == "ok"
    garbage = tmp_path / "garbage"
    garbage.write_bytes(os.urandom(256 * 1024))
    assert GraphOpenProbe(timeout_s=60)(garbage).outcome in {"exit_nonzero", "crashed"}


def test_the_frozen_subcommand_runs_the_same_probe(tmp_path) -> None:
    kuzu = pytest.importorskip("kuzu")
    graph = tmp_path / "graph"
    kuzu.Database(str(graph)).close()

    def command(path: Path, alarm_s: int) -> list[str]:
        return [
            sys.executable,
            "-m",
            "one_memory.cli",
            "--graph-open-probe",
            str(path),
            str(alarm_s),
        ]

    assert GraphOpenProbe(command=command, timeout_s=60)(graph).outcome == "ok"
    garbage = tmp_path / "garbage"
    garbage.write_bytes(os.urandom(256 * 1024))
    assert GraphOpenProbe(command=command, timeout_s=60)(garbage).condemns_graph


def test_probe_budget_and_cap_are_bounded_and_configurable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ONE_MEMORY_GRAPH_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ONE_MEMORY_GRAPH_PROBE_MAX_RSS_MB", raising=False)
    small = _graph(tmp_path)
    assert probe_timeout_s(small) == pytest.approx(60, abs=0.1)
    huge = tmp_path / "huge"
    with huge.open("wb") as handle:
        handle.truncate(10 * 1024 * 1024 * 1024)  # sparse
    assert probe_timeout_s(huge) == 120
    assert probe_max_rss_bytes() == 2048 * 1024 * 1024
    monkeypatch.setenv("ONE_MEMORY_GRAPH_PROBE_TIMEOUT_S", "15")
    monkeypatch.setenv("ONE_MEMORY_GRAPH_PROBE_MAX_RSS_MB", "512")
    assert probe_timeout_s(small) == 15
    assert probe_max_rss_bytes() == 512 * 1024 * 1024


# --- the marker lifecycle -----------------------------------------------------------


def test_the_marker_is_written_before_the_probe_and_cleared_after_it(tmp_path) -> None:
    graph = _graph(tmp_path)
    probe = _StubProbe(tmp_path, budget=7.0)
    guard = _guard(tmp_path, probe, instance_id="core-1")
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"

    marker = probe.seen_marker
    assert isinstance(marker, dict)
    assert marker["attempts"] == 1 and marker["graph_path"] == str(graph)
    receipt = probe.seen_receipt
    assert isinstance(receipt, dict)
    assert receipt["phase"] == "graph_open" and receipt["instance_id"] == "core-1"
    # The probe budget plus the margin: bounded, so the Runtime can be strict.
    assert receipt["budget_ms"] == 7_000 + 30_000
    assert _marker(tmp_path) is None
    assert not (tmp_path / STARTUP_RECEIPT_NAME).exists()
    assert guard.status().state == "ready"
    assert graph.exists() and _quarantined(graph) == []


def test_a_clean_probe_after_one_dead_start_does_not_quarantine(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 1, "graph_path": str(graph), "started_at": "x"})
    )
    probe = _StubProbe(tmp_path)
    guard = _guard(tmp_path, probe)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"
    assert isinstance(probe.seen_marker, dict) and probe.seen_marker["attempts"] == 2
    assert _marker(tmp_path) is None and graph.exists()


def test_a_missing_graph_clears_a_stale_marker_and_probes_nothing(tmp_path) -> None:
    graph = tmp_path / "hy-memory" / "governed-v2" / "graph"
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 5, "graph_path": str(graph), "started_at": "x"})
    )
    probe = _StubProbe(tmp_path)
    assert _guard(tmp_path, probe).open_at_startup(graph, switch_in_progress=False) == "absent"
    assert probe.calls == [] and _marker(tmp_path) is None


def test_failures_on_another_graph_are_not_counted(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 9, "graph_path": str(graph) + "-old", "started_at": "x"})
    )
    assert _guard(tmp_path).open_at_startup(graph, switch_in_progress=False) == "opened"
    assert graph.exists()


def test_two_starts_where_core_died_during_the_probe_quarantine_without_probing(
    tmp_path,
) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    probe = _StubProbe(tmp_path)
    guard = _guard(tmp_path, probe)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert probe.calls == []
    assert guard.status().last_quarantine_reason == "core_died_during_probe"


# --- the first failed probe quarantines -------------------------------------------------


@pytest.mark.parametrize("outcome", ["timeout", "rss_cap", "crashed", "exit_nonzero"])
def test_the_first_failed_probe_quarantines_by_renaming(tmp_path, outcome) -> None:
    graph = _graph(tmp_path)
    guard = _guard(tmp_path, _StubProbe(tmp_path, outcome))
    assert guard.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert not graph.exists() and not graph.with_name("graph.wal").exists()
    [stem] = _quarantined(graph)
    assert stem.read_bytes() == b"kuzu" * 1024
    assert stem.with_name(f"{stem.name}.wal").read_bytes() == b"wal"
    assert _marker(tmp_path) is None
    status = guard.status()
    assert status.state == "rebuilding_after_quarantine"
    assert status.quarantine_count == 1 and status.last_quarantined_at is not None
    assert status.last_quarantine_reason == outcome
    ledger = json.loads((tmp_path / QUARANTINE_LEDGER_NAME).read_text())
    assert ledger["rebuild_pending"] is True


def test_an_unavailable_probe_leaves_the_graph_alone(tmp_path) -> None:
    graph = _graph(tmp_path)
    guard = _guard(tmp_path, _StubProbe(tmp_path, "unavailable"))
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"
    assert graph.exists() and _quarantined(graph) == []
    assert _marker(tmp_path) is None


@pytest.mark.parametrize(
    ("source", "cap_mb", "timeout_s"),
    [
        ("import time; time.sleep(60)", 2048, 0.5),
        (
            "import time\nblock = bytearray(300 * 1024 * 1024)\n"
            "for i in range(0, len(block), 4096): block[i] = 1\ntime.sleep(60)",
            50,
            30,
        ),
        ("raise SystemExit(1)", 2048, 30),
    ],
    ids=["sleeps_past_budget", "allocates_past_cap", "exits_non_zero"],
)
def test_real_failing_probes_quarantine_on_the_first_failure(
    tmp_path, source, cap_mb, timeout_s
) -> None:
    graph = _graph(tmp_path)
    probe = GraphOpenProbe(
        command=_python(source),  # type: ignore[arg-type]
        timeout_s=timeout_s,
        max_rss_bytes=cap_mb * 1024 * 1024,
        poll_interval_s=0.02,
    )
    guard = _guard(tmp_path, probe)
    assert guard.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert not graph.exists() and len(_quarantined(graph)) == 1


def test_a_real_clean_probe_does_not_quarantine(tmp_path) -> None:
    graph = _graph(tmp_path)
    probe = GraphOpenProbe(command=_python("pass"), timeout_s=30)  # type: ignore[arg-type]
    assert _guard(tmp_path, probe).open_at_startup(graph, switch_in_progress=False) == "opened"
    assert graph.exists() and _quarantined(graph) == []


def test_only_the_newest_quarantined_copies_are_kept(tmp_path) -> None:
    clock = _Clock()
    for _ in range(KEEP_QUARANTINED + 2):
        graph = _graph(tmp_path)
        GraphOpenGuard(tmp_path, probe=_StubProbe(tmp_path), now=clock).quarantine(
            graph, failed_attempts=1
        )
    kept = sorted(p.name for p in _quarantined(graph))
    assert len(kept) == KEEP_QUARANTINED
    assert sorted(p.name for p in graph.parent.iterdir() if p.name.endswith(".wal")) == [
        f"{name}.wal" for name in kept
    ]
    ledger = json.loads((tmp_path / QUARANTINE_LEDGER_NAME).read_text())
    assert ledger["count"] == KEEP_QUARANTINED + 2


def test_the_rebuild_state_settles_once_the_rebuild_is_done(tmp_path) -> None:
    graph = _graph(tmp_path)
    guard = _guard(tmp_path, _StubProbe(tmp_path, "timeout"))
    guard.open_at_startup(graph, switch_in_progress=False)
    assert guard.status(rebuild_running=True).state == "rebuilding_after_quarantine"
    assert guard.status(rebuild_running=None).state == "rebuilding_after_quarantine"
    settled = guard.status(rebuild_running=False)
    assert settled.state == "ready" and settled.quarantine_count == 1
    later = _guard(tmp_path)
    _graph(tmp_path)
    later.open_at_startup(graph, switch_in_progress=False)
    assert later.status().state == "ready" and later.status().quarantine_count == 1


# --- embedding switch ------------------------------------------------------------


def test_no_quarantine_while_an_embedding_switch_is_open(tmp_path) -> None:
    graph = _graph(tmp_path)
    staging = graph.with_name("graph-abcdef12")
    staging.write_bytes(b"staging")
    probe = _StubProbe(tmp_path, "rss_cap")
    guard = _guard(tmp_path, probe)
    assert guard.open_at_startup(graph, switch_in_progress=True) == "failed"
    # Neither the live graph (the switch's source) nor the staging graph moves.
    assert probe.calls == [graph]
    assert graph.exists() and staging.read_bytes() == b"staging"
    assert guard.status().state == "unavailable"
    assert guard.status().quarantine_count == 0
    marker = _marker(tmp_path)
    assert marker is not None and marker["attempts"] == 1
    # After the switch, the next failed probe quarantines; staging untouched.
    after = _guard(tmp_path, _StubProbe(tmp_path, "rss_cap"))
    assert after.open_at_startup(graph, switch_in_progress=False) == "quarantined"
    assert staging.read_bytes() == b"staging"


def test_a_known_bad_graph_is_not_probed_again_during_a_switch(tmp_path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / MARKER_NAME).write_text(
        json.dumps({"attempts": 2, "graph_path": str(graph), "started_at": "x"})
    )
    probe = _StubProbe(tmp_path)
    guard = _guard(tmp_path, probe)
    assert guard.open_at_startup(graph, switch_in_progress=True) == "skipped"
    assert probe.calls == [] and graph.exists()
    assert guard.status().state == "unavailable"


# --- Core itself dying during the probe -------------------------------------------


_CORE = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from one_memory.graph_open_guard import GraphOpenGuard, ProbeResult

    data_dir, graph, behaviour = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

    class Probe:
        def budget_s(self, _path):
            return 5.0

        def __call__(self, _path):
            if behaviour == "die":
                # Core killed under memory pressure: no cleanup at all.
                os._exit(137)
            if behaviour == "hang":
                while True:
                    time.sleep(0.05)
            return ProbeResult("ok")

    outcome = GraphOpenGuard(data_dir, probe=Probe()).open_at_startup(
        graph, switch_in_progress=False
    )
    print(outcome)
    """
)


def _start(tmp_path: Path, graph: Path, behaviour: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _CORE, str(tmp_path), str(graph), behaviour],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_cores_killed_during_the_probe_lead_to_quarantine_on_the_third(tmp_path) -> None:
    graph = _graph(tmp_path)
    for attempt in (1, 2):
        child = _start(tmp_path, graph, "die")
        assert child.wait(timeout=60) == 137
        marker = _marker(tmp_path)
        assert marker is not None and marker["attempts"] == attempt
        assert graph.exists()
    third = _start(tmp_path, graph, "ok")
    out, err = third.communicate(timeout=60)
    assert third.returncode == 0, err
    assert out.strip() == "quarantined"
    assert not graph.exists() and len(_quarantined(graph)) == 1
    assert _marker(tmp_path) is None


def test_a_core_killed_while_the_probe_runs_leaves_the_marker(tmp_path) -> None:
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


def _run_core(tmp_path, monkeypatch, *, mode: str, outcome: str, scenario) -> list[Path]:
    monkeypatch.delenv("ONE_MEMORY_GRAPH_STORE", raising=False)
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    ConfigStore(settings.data_dir).set_memory_mode(mode)  # type: ignore[arg-type]
    probes: list[Path] = []

    class Probe:
        def budget_s(self, _path: Path) -> float:
            return 1.0

        def __call__(self, path: Path) -> ProbeResult:
            probes.append(path)
            return ProbeResult(outcome)  # type: ignore[arg-type]

    original_init = GraphOpenGuard.__init__

    def init(self, data_dir, **kwargs):
        kwargs.setdefault("probe", Probe())
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
    return probes


@pytest.mark.fresh_sdk
def test_ultra_startup_probes_the_graph_before_ready(tmp_path, monkeypatch) -> None:
    graph = live_graph_path(tmp_path / "data", "graph")
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_bytes(b"kuzu")

    async def scenario(app, _sdk, client):
        assert app.state.graph_open_outcome == "opened"
        body = (await client.get("/api/v1/memory-processing")).json()
        assert body["pipeline"]["graph_store"] == {
            "state": "ready",
            "quarantine_count": 0,
            "last_quarantined_at": None,
            "last_quarantine_reason": None,
            "failed_open_attempts": 0,
        }
        mode = (await client.get("/api/v1/settings/memory-mode")).json()
        assert mode["graph_store"]["state"] == "ready"

    probes = _run_core(tmp_path, monkeypatch, mode="ultra", outcome="ok", scenario=scenario)
    assert probes == [graph]
    assert not (tmp_path / "data" / MARKER_NAME).exists()


@pytest.mark.fresh_sdk
def test_pro_startup_never_probes_the_graph(tmp_path, monkeypatch) -> None:
    async def scenario(app, _sdk, client):
        body = (await client.get("/api/v1/memory-processing")).json()
        assert body["pipeline"]["graph_store"]["state"] == "not_used"

    probes = _run_core(tmp_path, monkeypatch, mode="pro", outcome="ok", scenario=scenario)
    assert probes == []


@pytest.mark.fresh_sdk
def test_ultra_startup_with_a_failed_probe_quarantines_and_rebuilds(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    graph = live_graph_path(data_dir, "graph")
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_bytes(b"bloated")

    async def scenario(app, sdk, client):
        assert app.state.graph_open_outcome == "quarantined"
        await app.state.memory_graph_backfill.wait()
        # The normal per-user rebuild, from the SDK's memories.
        assert sdk.graph_builds == ["local"]
        body = (await client.get("/api/v1/memory-processing")).json()
        graph_store = body["pipeline"]["graph_store"]
        assert graph_store["quarantine_count"] == 1
        assert graph_store["last_quarantine_reason"] == "rss_cap"
        assert graph_store["state"] == "ready"

    _run_core(tmp_path, monkeypatch, mode="ultra", outcome="rss_cap", scenario=scenario)
    # Whatever the SDK opened afterwards is a fresh graph, not the bloated one.
    assert not graph.is_file() or graph.read_bytes() != b"bloated"
    quarantined = [p for p in _quarantined(graph) if p.is_file()]
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == b"bloated"
    assert not (data_dir / MARKER_NAME).exists()


# --- the width read never opens Kuzu in Core -----------------------------------------


def _memory_graph(path: Path, width: int) -> None:
    kuzu = pytest.importorskip("kuzu")
    database = kuzu.Database(str(path))
    connection = kuzu.Connection(database)
    connection.execute(
        f"CREATE NODE TABLE Memory(id STRING, embedding FLOAT[{width}], PRIMARY KEY(id))"
    )
    connection.close()
    database.close()


def test_the_width_is_read_in_the_child_not_in_core(tmp_path, monkeypatch) -> None:
    kuzu = pytest.importorskip("kuzu")
    from one_memory import hy_graph_admin

    graph = tmp_path / "graph"
    _memory_graph(graph, 384)
    hy_graph_admin.forget_graph_widths()

    def no_open_in_core(*_args, **_kwargs):
        raise AssertionError("kuzu.Database opened inside Core")

    monkeypatch.setattr(kuzu, "Database", no_open_in_core)
    try:
        assert hy_graph_admin._graph_embedding_width(graph) == 384
    finally:
        hy_graph_admin.forget_graph_widths()


def test_a_clean_startup_probe_primes_the_width_cache(tmp_path, monkeypatch) -> None:
    pytest.importorskip("kuzu")
    from one_memory import hy_graph_admin

    graph = tmp_path / "graph"
    _memory_graph(graph, 512)
    hy_graph_admin.forget_graph_widths()
    guard = _guard(tmp_path, GraphOpenProbe(timeout_s=60))
    assert guard.open_at_startup(graph, switch_in_progress=False) == "opened"

    class _NoChild:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("a second probe ran for a cached width")

    monkeypatch.setattr("one_memory.graph_open_guard.GraphOpenProbe", _NoChild)
    try:
        assert hy_graph_admin._graph_embedding_width(graph) == 512
    finally:
        hy_graph_admin.forget_graph_widths()


def test_the_status_helper_reports_rebuilding_until_recovery_has_run(tmp_path) -> None:
    graph = _graph(tmp_path)
    guard = _guard(tmp_path, _StubProbe(tmp_path, "timeout"))
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
