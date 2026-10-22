"""D93 test 2: read latency while pure-Python work holds the GIL.

Plan `读写隔离方案-20261009.html` section 8 row 2, fixing the D92 experiment
as a test. Three pure-Python busy threads stand in for Core's background work
(scan, extraction bookkeeping, recompute). Meanwhile the home summary, the
memory library page and the map are requested in a loop; every non-200 is an
error and latencies give p95.

Two targets:

* `core` (default): the routes are served by `api.create_app` in this same
  process, next to the busy threads. This is today's architecture and gives
  the baseline. It is expected to be slow; it is measured, not gated, unless
  `ONE_MEMORY_D93_GATE=1`.
* `reader`: B's Reader is started as a separate process
  (`one-memory serve --reader`) on the same library, the busy threads stay in
  this process. This is the claim the plan makes: work in Core no longer
  touches reads. Gated: 0 errors, home and library p95 below 1 second.

Knobs: ONE_MEMORY_D93_LOAD_SECONDS (gate 600, default 20),
ONE_MEMORY_D93_MEMORIES (default 600), ONE_MEMORY_D93_TARGET,
ONE_MEMORY_D93_REPORT (JSON path for the numbers).
Recall ("at least one hit on a known question") needs the HY supervisor and is
not exercised here; it stays with acceptance item 7b.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from one_memory import api
from one_memory.config import Settings
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_tasks import MemoryTaskOutput, MemoryTaskStore, evidence_ref
from tests.person_door import as_person

SECONDS = float(os.environ.get("ONE_MEMORY_D93_LOAD_SECONDS", "20"))
MEMORIES = int(os.environ.get("ONE_MEMORY_D93_MEMORIES", "600"))
TARGET = os.environ.get("ONE_MEMORY_D93_TARGET", "core")
GATE = os.environ.get("ONE_MEMORY_D93_GATE", "1" if TARGET == "reader" else "") == "1"
TOKEN = "d93-load-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ROUTES = (
    ("home", "/api/v1/home-summary", {}),
    ("library", "/api/v1/memory-library", {"limit": 100, "active_only": "true"}),
    ("map", "/api/v1/memories/graph", {"limit": 160}),
)


def _build_library(database: Path) -> None:
    now = "2026-10-09T00:00:00+00:00"
    with sqlite3.connect(database) as setup:
        setup.executescript(GRANT_SCHEMA)
        for source in ("cursor", "codex"):
            setup.execute(
                "INSERT INTO source_grants "
                "(user_id,source_id,root_path,requested_path,root_identity,"
                "candidate_kind,granted_at,revoked_at,revision) "
                "VALUES ('local',?,?,?,'fixture','default',?,NULL,1)",
                (source, f"/fixture/{source}", f"/fixture/{source}", now),
            )
    evidence = EvidenceStore(database)
    tasks = MemoryTaskStore(database)
    person = as_person(MemoryGovernanceStore(database))
    for index in range(MEMORIES):
        record = evidence.put(
            EvidenceInput(
                source_id=("cursor", "codex")[index % 2],
                conversation_id=f"conversation-{index // 10}",
                message_id=f"m-{index}",
                correlation_id=f"m-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"第 {index} 条对话原文，讨论读写隔离和记忆页的加载速度。" * 4,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        memory = person.create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=f"记忆 {index}：读写隔离要让记忆页在后台忙时也能打开。",
                scopes=("all-apps",),
                evidence_ids=(record.evidence_id,),
            ),
            request_id=f"create-{index}",
        )
        tasks.save_output(
            MemoryTaskOutput(
                task_id=f"task-{index}",
                memory_id=memory.memory_id,
                memory_revision=memory.revision,
                hy_memory_id=f"hy-{index}",
                owner="user",
                layer="L2_FACT",
                engine_version="fixture",
                support_refs=(evidence_ref(record),),
            )
        )


class _Busy:
    """Three pure-Python threads: no I/O, no sleep, they only want the GIL."""

    def __init__(self, count: int = 3) -> None:
        self.stop = threading.Event()
        self.loops = [0] * count
        self.threads = [
            threading.Thread(target=self._spin, args=(index,), daemon=True)
            for index in range(count)
        ]

    def _spin(self, index: int) -> None:
        while not self.stop.is_set():
            total = 0
            for value in range(20_000):
                total += value * value % 7
            self.loops[index] += 1

    def __enter__(self):
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=5)


_SPIN = """
import threading
def spin():
    while True:
        total = 0
        for value in range(20_000):
            total += value * value % 7
for _ in range(3):
    threading.Thread(target=spin, daemon=True).start()
threading.Event().wait()
"""


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


async def _drive(client: httpx.AsyncClient, deadline: float) -> dict:
    stats = {name: {"ok": 0, "errors": {}, "latency": []} for name, _, _ in ROUTES}
    while time.monotonic() < deadline:
        for name, url, params in ROUTES:
            started = time.monotonic()
            try:
                response = await client.get(url, params=params, headers=AUTH, timeout=30)
                code = str(response.status_code)
            except httpx.HTTPError as error:
                code = type(error).__name__
            elapsed = time.monotonic() - started
            entry = stats[name]
            entry["latency"].append(elapsed)
            if code == "200":
                entry["ok"] += 1
            else:
                entry["errors"][code] = entry["errors"].get(code, 0) + 1
    return {
        name: {
            "requests": len(entry["latency"]),
            "ok": entry["ok"],
            "errors": entry["errors"],
            "p95_seconds": _p95(entry["latency"]),
            "max_seconds": max(entry["latency"], default=None),
        }
        for name, entry in stats.items()
    }


def _core_app(data_dir: Path, root: Path):
    """Core as the app runs it (lifespan included), background processing off."""
    return api.create_app(
        Settings(
            data_dir=data_dir,
            source_home=root / "home",
            api_token=SecretStr(TOKEN),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _start_reader(data_dir: Path) -> tuple[subprocess.Popen, str]:
    pytest.importorskip("one_memory.reader_app", reason="B (Reader) not in this build")
    port = _free_port()
    env = {
        **os.environ,
        "ONE_MEMORY_DATA_DIR": str(data_dir),
        "ONE_MEMORY_READER_PORT": str(port),
        "ONE_MEMORY_API_TOKEN": TOKEN,
        "ONE_MEMORY_CORE_PORT": str(_free_port()),  # nothing there: Core-only parts degrade
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "one_memory.cli", "serve", "--reader"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code < 500:
                return process, base
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise AssertionError(f"Reader exited: {process.stderr.read()[-2000:]!r}")
        time.sleep(0.1)
    process.kill()
    raise AssertionError("Reader did not answer /health within 10 seconds")


def test_reads_stay_up_while_python_threads_hold_the_gil(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = data_dir / "one-memory.sqlite3"
    _build_library(database)

    async def run(client):
        # Warm once outside the measurement so lazy imports do not count.
        for _, url, params in ROUTES:
            await client.get(url, params=params, headers=AUTH, timeout=30)
        idle = await _drive(client, time.monotonic() + min(5.0, SECONDS))
        if TARGET == "reader":
            # The busy threads belong to Core, not to this client: put them in
            # a process of their own so the client does not queue for the GIL.
            spinner = subprocess.Popen([sys.executable, "-c", _SPIN])
            try:
                loaded = await _drive(client, time.monotonic() + SECONDS)
            finally:
                spinner.terminate()
                spinner.wait(timeout=10)
            return idle, loaded, [1, 1, 1]
        with _Busy() as busy:
            loaded = await _drive(client, time.monotonic() + SECONDS)
        return idle, loaded, busy.loops

    reader = None
    try:
        if TARGET == "reader":
            reader, base = _start_reader(data_dir)
            transport = httpx.AsyncHTTPTransport()
            application = None
        else:
            base = "http://test"
            application = _core_app(data_dir, tmp_path)
            transport = httpx.ASGITransport(app=application)

        async def main():
            async with httpx.AsyncClient(transport=transport, base_url=base) as client:
                if application is None:
                    return await run(client)
                async with application.router.lifespan_context(application):
                    return await run(client)

        idle, loaded, loops = asyncio.run(main())
    finally:
        if reader is not None:
            reader.terminate()
            reader.wait(timeout=10)

    report = {
        "target": TARGET,
        "memories": MEMORIES,
        "seconds": SECONDS,
        "busy_loops": loops,
        "idle": idle,
        "loaded": loaded,
    }
    path = os.environ.get("ONE_MEMORY_D93_REPORT")
    if path:
        Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))

    assert all(count > 0 for count in loops), "busy threads never ran"
    assert all(entry["requests"] > 0 for entry in loaded.values())
    if GATE:
        for name, entry in loaded.items():
            assert entry["errors"] == {}, f"{name}: {entry['errors']}"
        for name in ("home", "library"):
            assert loaded[name]["p95_seconds"] < 1.0, f"{name} p95 {loaded[name]['p95_seconds']}"
