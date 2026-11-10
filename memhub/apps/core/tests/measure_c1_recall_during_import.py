"""Prove recall does not degrade during a large import (read-only measurement).

The objective asks for this at 100k/430k Evidence scale, without freezing
WorkBuddy or the UI main thread for more than a second. What C1 can measure
without touching C4's code: run a real import in the background and, while it
runs, keep calling the product's read endpoint `/api/v1/recall`, then compare
against an idle baseline on the same data.

It reports: idle p50/p95/max, during-import p50/p95/max, how many samples went
over the 1s freeze threshold, and whether the import still completed.
"""

import argparse
import asyncio
import json
import socket
import sqlite3
import time
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.config import Settings
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    build_source_catalog,
)
from one_memory.imports.supervisor import ImportSupervisor

FREEZE_THRESHOLD_SECONDS = 1.0


def _database_locked_events(log_dir: Path) -> int:
    total = 0
    for path in log_dir.glob("diagnostic-*.jsonl"):
        total += path.read_text(encoding="utf-8", errors="replace").count(
            '"error_code":"database_locked"'
        )
    return total


def _diagnostic_event_counts(log_dir: Path, names: set[str]) -> dict[str, int]:
    counts = {name: 0 for name in names}
    for path in log_dir.glob("diagnostic-*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = event.get("event")
            if name in counts:
                counts[name] += 1
    return counts


def _record(source: str, index: int) -> dict:
    text = f"scale record {index} about budget decision"
    if source == "workbuddy":
        # The real WorkBuddy shape: a role `type`, a timestamp, and a nested
        # message whose content is the text.
        return {
            "type": "user" if index % 2 == 0 else "assistant",
            "timestamp": "2026-09-28T10:00:00+08:00",
            "message": {"content": text},
        }
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _write_corpus(root: Path, records: int, source: str = "codex") -> Path:
    if source == "workbuddy":
        path = root / "projects" / "workbuddy-scale.jsonl"
    else:
        path = root / "project" / "rollout-scale.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for index in range(records):
            stream.write(json.dumps(_record(source, index)) + "\n")
    return path


async def _measure_recall(client, samples: int, interval: float) -> tuple[list[float], int]:
    latencies = []
    unavailable = 0
    for _ in range(samples):
        started = time.perf_counter()
        response = await client.post(
            "/api/v1/recall",
            json={"query": "budget decision", "scope": "all-apps", "limit": 8},
        )
        elapsed = time.perf_counter() - started
        if response.status_code == 503:
            unavailable += 1
            await asyncio.sleep(interval)
            continue
        if response.status_code != 200:
            raise AssertionError(
                f"unexpected recall status {response.status_code}: {response.text[:200]}"
            )
        latencies.append(elapsed)
        await asyncio.sleep(interval)
    return latencies, unavailable


async def _measure_recall_until(
    client,
    supervisor,
    job_id: str,
    samples: int,
    interval: float,
    *,
    timeout: float = 900.0,
) -> tuple[list[float], int, bool]:
    """Keep sampling until the import reaches a terminal state, not a fixed count."""
    latencies: list[float] = []
    unavailable = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        started = time.perf_counter()
        response = await client.post(
            "/api/v1/recall",
            json={"query": "budget decision", "scope": "all-apps", "limit": 8},
        )
        elapsed = time.perf_counter() - started
        if response.status_code == 503:
            unavailable += 1
        elif response.status_code == 200:
            latencies.append(elapsed)
        else:
            raise AssertionError(
                f"unexpected recall status {response.status_code}: {response.text[:200]}"
            )
        current = await asyncio.to_thread(supervisor.job_store.require, job_id)
        if current.status.value in {"completed", "failed", "cancelled", "interrupted", "paused"}:
            return latencies, unavailable, True
        if len(latencies) >= samples and time.monotonic() >= deadline:
            break
        await asyncio.sleep(interval)
    return latencies, unavailable, False


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "samples": len(values),
        "p50_ms": round(ordered[len(ordered) // 2] * 1000, 1),
        "p95_ms": round(ordered[index] * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
        "over_one_second": sum(1 for value in values if value > FREEZE_THRESHOLD_SECONDS),
    }


async def _run(
    tmp: Path, records: int, samples: int, interval: float, source: str = "codex"
) -> dict:
    def no_external(*_args, **_kwargs):
        raise AssertionError("this measurement must not reach the network")

    socket.socket.connect = no_external  # type: ignore[assignment]
    socket.getaddrinfo = no_external  # type: ignore[assignment]

    home = tmp / "home"
    (home / ".cursor").mkdir(parents=True, exist_ok=True)
    if source == "workbuddy":
        (home / ".codebuddy" / "projects").mkdir(parents=True, exist_ok=True)
    catalog = build_source_catalog(home=home, environment={})
    root = catalog[source].candidate.path
    root.mkdir(parents=True, exist_ok=True)
    _write_corpus(root, records, source)

    settings = Settings(
        _env_file=None,
        data_dir=tmp / "data",
        source_home=home,
        api_token=SecretStr("synthetic"),
        live_sync_enabled=False,
        automatic_memory_processing=False,
    )
    from one_memory.api import create_app

    app = create_app(settings, source_catalog=catalog)
    headers = {"Authorization": "Bearer synthetic"}
    result: dict = {"records": records, "source": source}

    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=headers
        ) as client,
    ):
        # Idle baseline first: no import running.
        idle, idle_unavailable = await _measure_recall(client, samples, interval)
        result["idle"] = _stats(idle)
        result["idle_unavailable"] = idle_unavailable

        grants = SourceGrantStore(app.state.evidence_store.database_path, home=home)
        grants.grant(
            user_id="local",
            source_id=source,
            root=root,
            candidate_kind=RootCandidateKind.DEFAULT,
        )
        app.state.config_store.set_sync(source, True)
        supervisor = ImportSupervisor(
            adapters={
                source: catalog[source].create_adapter(
                    grants.authorized_root(user_id="local", source_id=source)
                )
            },
            evidence_store=app.state.evidence_store,
            job_store=ImportJobStore(app.state.evidence_store.database_path),
        )
        job = supervisor.create_job(source)
        supervisor.enqueue(job.job_id)

        hook_text = "realtime hook while history import is running"
        hook_started = time.monotonic()
        # A hook hands its turn to Core over loopback; it no longer writes the
        # database itself. What is measured is that hand-off under import load.
        handed = await client.post(
            "/api/v1/live-capture/workbuddy",
            json={
                "hook_event_name": "UserPromptSubmit",
                "session_id": "pressure-live-session",
                "prompt": hook_text,
            },
        )
        result["hook_accepted"] = handed.status_code == 202
        result["hook_visible_ms"] = round((time.monotonic() - hook_started) * 1000, 1)

        during, during_unavailable, sampled_terminal = await _measure_recall_until(
            client, supervisor, job.job_id, samples, interval
        )
        result["during_import"] = _stats(during)
        result["during_unavailable"] = during_unavailable
        result["recall_sampling_terminal"] = sampled_terminal

        final = await supervisor.wait(job.job_id)
        result["import_status"] = str(final.status)
        result["stored"] = final.stored
    with sqlite3.connect(app.state.evidence_store.database_path) as connection:
        result["evidence_rows"] = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    result["database_locked_events"] = _database_locked_events(settings.data_dir / "logs")
    result["realtime_error_events"] = _diagnostic_event_counts(
        settings.data_dir / "logs",
        {"import.heartbeat_failed", "live_sync.internal_error", "live_sync.hook_failed"},
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--tmp", default="")
    parser.add_argument(
        "--source",
        default="codex",
        choices=["codex", "workbuddy"],
        help="which real record shape to generate (default: codex)",
    )
    args = parser.parse_args()

    if args.tmp:
        base = Path(args.tmp).resolve()
        base.mkdir(parents=True, exist_ok=True)
        print(
            json.dumps(
                asyncio.run(_run(base, args.records, args.samples, args.interval, args.source)),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    import tempfile

    with tempfile.TemporaryDirectory(prefix="c1-recall-") as directory:
        base = Path(directory).resolve()
        print(
            json.dumps(
                asyncio.run(_run(base, args.records, args.samples, args.interval, args.source)),
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
