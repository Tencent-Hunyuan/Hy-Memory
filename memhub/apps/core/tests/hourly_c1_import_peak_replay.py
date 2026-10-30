"""Package-level O-17 import-peak replay for a built hourly candidate.

O-17 is the real stress case: a large production-shaped import competes with
recall reads and can surface `database is locked`. This probe drives the
*packaged* Core/CLI inside an isolated HOME/data directory, imports a
production-shaped corpus, keeps issuing recall requests, and reports:

- first/last visible import status and whether the job completed;
- recall latency during import vs idle;
- count of recall samples over the one-second freeze threshold;
- the real `database.error` / `database_locked` event count from the package's
  own diagnostic log;
- source-file hashes and cleanup state.

The probe measures subprocess end-to-end recall calls, including CLI startup,
so its latency is deliberately a conservative upper bound. A value over one
second is never washed into a pass: it is recorded as a failed run and must be
reproduced in a quieter package run before it can support a performance claim.

It deliberately does not pretend a synthetic injected lock is a production
replay. The `--records` scale is caller-selected so H24/H48 can run the real
430k-scale case on a machine with enough disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _run(cli: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(cli), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def _json(text: str) -> dict:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_corpus(root: Path, records: int) -> Path:
    path = root / "projects" / "peak-replay.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for index in range(records):
            stream.write(
                json.dumps(
                    {
                        "type": "user" if index % 2 == 0 else "assistant",
                        "sessionId": "peak-replay",
                        "timestamp": "2026-09-30T10:00:00+08:00",
                        "message": {"content": f"peak replay record {index} budget decision"},
                    }
                )
                + "\n"
            )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recall_sample(cli: Path, env: dict[str, str]) -> tuple[float, int]:
    started = time.perf_counter()
    result = _run(
        cli,
        [
            "recall",
            "--query",
            "budget decision",
            "--scope",
            "all-apps",
            "--limit",
            "8",
            "--json",
        ],
        env,
    )
    return time.perf_counter() - started, result.returncode


def _database_locked_count(log_dir: Path) -> int:
    count = 0
    for path in sorted(log_dir.glob("diagnostic-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"error_code":"database_locked"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "database.error":
                count += 1
    return count


def _import_receipt(cli: Path, env: dict[str, str], plan: dict) -> dict:
    request_id = (plan.get("plan") or {}).get("request_id")
    if not request_id:
        return {}
    parsed = _json(_run(cli, ["sources", "receipt", str(request_id), "--json"], env).stdout)
    return parsed.get("receipt", parsed)


def _import_is_terminal(receipt: dict) -> bool:
    jobs = receipt.get("jobs", []) if isinstance(receipt, dict) else []
    return bool(jobs) and all(
        job.get("status") in {"completed", "failed", "cancelled", "interrupted"} for job in jobs
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--recall-samples", type=int, default=40)
    parser.add_argument("--recall-interval", type=float, default=0.05)
    parser.add_argument(
        "--require-locked",
        action="store_true",
        help="fail unless at least one real database_locked event is observed",
    )
    args = parser.parse_args()

    package = Path(args.package).resolve()
    cli = package / "artifacts/runtime-dist/bin/one-memory"
    if not cli.exists():
        print(json.dumps({"state": "blocked", "reason": "cli_missing", "cli": str(cli)}))
        return 2

    work = Path(tempfile.mkdtemp(prefix="c1-peak-replay-"))
    home = work / "home"
    data = work / "data"
    corpus = home / ".codebuddy" / "projects"
    corpus.mkdir(parents=True)
    data.mkdir(parents=True)
    source = _write_corpus(corpus, args.records)
    before = _sha256(source)
    env = dict(os.environ)
    env.update(
        {
            "ONE_MEMORY_DATA_DIR": str(data),
            "ONE_MEMORY_SOURCE_HOME": str(home),
            "ONE_MEMORY_HOST_HOME": str(home),
            "CODEBUDDY_CONFIG_DIR": str(home / ".codebuddy"),
            "ONE_MEMORY_AUTOMATIC_MEMORY_PROCESSING": "false",
            "ONE_MEMORY_LIVE_SYNC_ENABLED": "false",
            "ONE_MEMORY_PORT": str(_free_port()),
        }
    )
    env.pop("ONE_MEMORY_API_TOKEN", None)

    receipt: dict = {
        "schema_version": 1,
        "package": package.name,
        "records": args.records,
    }
    process = None
    try:
        process = subprocess.Popen(
            [str(cli), "serve"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ready = {}
        for _ in range(120):
            ready = _json(_run(cli, ["status", "--json"], env).stdout)
            if ready.get("runtime", {}).get("state") == "running":
                break
            time.sleep(0.5)
        receipt["runtime"] = ready.get("runtime", {})
        if ready.get("runtime", {}).get("state") != "running":
            receipt["state"] = "blocked"
            receipt["reason"] = "runtime_not_ready"
            return 2

        _run(cli, ["sources", "connect", "workbuddy", "--json"], env)
        # No plan files: the connect request id is the idempotency key, minted
        # here so the receipt can be read while the import runs.
        plan = {"plan": {"request_id": str(uuid.uuid4())}}

        idle_latencies = []
        idle_errors: list[int] = []
        for _ in range(min(5, args.recall_samples)):
            latency, code = _recall_sample(cli, env)
            idle_latencies.append(latency)
            if code != 0:
                idle_errors.append(code)

        started = time.perf_counter()
        imported = _run(
            cli,
            [
                "sources",
                "connect",
                "workbuddy",
                "--import",
                "--request-id",
                plan["plan"]["request_id"],
                "--json",
            ],
            env,
        )
        during_latencies = []
        during_errors: list[int] = []
        samples = max(1, args.recall_samples)
        deadline = time.monotonic() + 900
        import_receipt: dict = {}
        while time.monotonic() < deadline:
            latency, code = _recall_sample(cli, env)
            during_latencies.append(latency)
            if code != 0:
                during_errors.append(code)
            import_receipt = _import_receipt(cli, env, plan)
            if _import_is_terminal(import_receipt):
                break
            if (
                imported.returncode != 0
                and len(during_latencies) >= samples
                and import_receipt.get("jobs")
            ):
                break
            time.sleep(args.recall_interval)

        receipt["import_exit"] = imported.returncode
        receipt["import_seconds"] = round(time.perf_counter() - started, 3)
        if not _import_is_terminal(import_receipt):
            import_receipt = _import_receipt(cli, env, plan)
        receipt["import_receipt"] = import_receipt
        receipt["recall_samples_during_import"] = len(during_latencies)
        receipt["recall_sampling_terminal"] = _import_is_terminal(import_receipt)
        receipt["idle_recall_error_codes"] = idle_errors
        receipt["idle_recall_max_ms"] = round(max(idle_latencies, default=0) * 1000, 1)
        receipt["during_recall_error_codes"] = during_errors
        receipt["during_recall_max_ms"] = round(max(during_latencies, default=0) * 1000, 1)
        receipt["during_recall_p95_ms"] = (
            round(
                sorted(during_latencies)[
                    min(len(during_latencies) - 1, int(len(during_latencies) * 0.95))
                ]
                * 1000,
                1,
            )
            if during_latencies
            else None
        )
        receipt["during_recall_over_1s"] = sum(latency > 1.0 for latency in during_latencies)
        receipt["database_locked_events"] = _database_locked_count(data / "logs")
        receipt["source_sha_unchanged"] = _sha256(source) == before

        status = _json(_run(cli, ["status", "--json"], env).stdout)
        receipt["final_status"] = status.get("sections", {}).get("sources", {})
        jobs = receipt["import_receipt"].get("jobs", [])
        stored = sum(int(job.get("stored", 0)) for job in jobs)
        lock_requirement_met = not args.require_locked or receipt["database_locked_events"] >= 1
        receipt["require_locked"] = args.require_locked
        receipt["state"] = (
            "passed"
            if imported.returncode == 0
            and receipt["source_sha_unchanged"]
            and stored == args.records
            and receipt["final_status"].get("imported_messages") == args.records
            and receipt["recall_sampling_terminal"]
            and not idle_errors
            and not during_errors
            and receipt["during_recall_over_1s"] == 0
            and lock_requirement_met
            else "failed"
        )
        return 0 if receipt["state"] == "passed" else 1
    finally:
        _run(cli, ["stop"], env)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(work, ignore_errors=True)
        receipt["cleaned"] = not work.exists()
        print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
