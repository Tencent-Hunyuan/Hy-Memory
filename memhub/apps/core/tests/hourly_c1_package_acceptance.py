"""Positive real-source acceptance against a built hourly package.

This is C1's package-level acceptance probe for the import chain. Unlike the
engine probe, it drives the package's own CLI in an isolated HOME/data directory
and never touches the daily data directory or a real host source.

The copied corpus is read-only in the source tree and writable only in the
throwaway HOME. On success the receipt proves:

1. history import stores real records;
2. an unchanged rerun stores nothing;
3. append imports only the new record;
4. a runtime restart resumes from the committed cursor;
5. revoke/read-off persists; and
6. the copied originals remain byte-identical.

Usage:
    uv run python tests/hourly_c1_package_acceptance.py \
        --package "/path/to/One-Memory-Hxx-candidate" \
        --origin "$HOME/.workbuddy/projects" --limit 2

Exit code 0 means every receipt check passed. A non-zero result means the
package failed acceptance; no state is written outside the temporary root.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from subprocess import Popen

# Full real corpora are the default. A nonzero cap is an explicit bounded
# diagnostic mode and is recorded in the receipt.
UNBOUNDED_FILE_BYTES = 0
CONVERGENCE_TIMEOUT_SECONDS = 900.0


class _StopRuntimeError(RuntimeError):
    """The package runtime did not stop cleanly before the next restart."""


def _run(cli: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(cli), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _body(text: str) -> dict:
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _nested(value: dict, *keys: str) -> dict:
    current: object = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _stop_runtime(cli: Path, env: dict[str, str], process: Popen[str]) -> None:
    """Stop the package runtime and wait through the service receipt cleanup."""
    stopped = _run(cli, ["stop"], env)
    for _ in range(80):
        if process.poll() is not None:
            break
        time.sleep(0.25)
    if process.poll() is None:
        if stopped.returncode == 0:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            raise RuntimeError("package runtime ignored a successful stop request")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    free_samples = 0
    for _ in range(60):
        if not _port_in_use(8765):
            free_samples += 1
            if free_samples >= 2:
                return
        else:
            free_samples = 0
        time.sleep(0.25)
    raise _StopRuntimeError("package runtime did not release port 8765 after stop")


def _cli_path(package: Path) -> Path:
    """Support nested-dist, explicit artifacts and flat installed layouts."""
    relative_paths = (
        "dist/bin/one-memory",
        "artifacts/runtime-dist/bin/one-memory",
        "bin/one-memory",
    )
    for relative in relative_paths:
        candidate = package / relative
        if candidate.exists():
            return candidate
    return package / relative_paths[0]


def _source_home(home: Path, source: str) -> Path:
    return {
        "workbuddy": home / ".workbuddy" / "projects",
        "codex": home / ".codex" / "sessions",
        "claude-code": home / ".claude" / "projects",
        "cursor": home / ".cursor" / "projects",
    }[source]


def _source_environment(home: Path, source: str) -> dict[str, str]:
    return {
        "workbuddy": {"WORKBUDDY_CONFIG_DIR": str(home / ".workbuddy")},
        "codex": {"CODEX_HOME": str(home / ".codex")},
        "claude-code": {"CLAUDE_CONFIG_DIR": str(home / ".claude")},
        "cursor": {},
    }[source]


def _append_record(source: str) -> dict:
    if source == "codex":
        return {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "package acceptance append"}],
            },
        }
    if source == "claude-code":
        return {
            "type": "user",
            "uuid": "package-acceptance-append",
            "sessionId": "package-acceptance-append",
            "timestamp": "2026-09-29T19:00:00+08:00",
            "message": {"content": [{"type": "text", "text": "package acceptance append"}]},
        }
    if source == "cursor":
        return {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "package acceptance append"}]},
        }
    return {
        "type": "user",
        "sessionId": "package-acceptance-append",
        "timestamp": "2026-09-29T19:00:00+08:00",
        "message": {"content": "package acceptance append"},
    }


def _wait_ready(
    cli: Path,
    env: dict[str, str],
    process: Popen[str] | None = None,
    *,
    attempts: int = 120,
) -> dict:
    last: dict = {}
    for _ in range(attempts):
        last = _body(_run(cli, ["status", "--json"], env).stdout)
        if last.get("runtime", {}).get("state") == "running":
            return last
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.5)
    return last


def _serve_output_tail(process: Popen[str] | None) -> str:
    if process is None or process.stdout is None:
        return ""
    try:
        output, _ = process.communicate(timeout=0.5)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return ""
    return (output or "")[-1200:]


def _receipt(
    cli: Path,
    env: dict[str, str],
    plan: dict,
    *,
    wait_terminal: bool = True,
    wait_timeout: float = CONVERGENCE_TIMEOUT_SECONDS,
) -> dict:
    request_id = _nested(plan, "plan").get("request_id")
    if not request_id:
        return {}
    last: dict = {}
    job_id: str | None = None
    deadline = time.monotonic() + wait_timeout
    while True:
        parsed = _body(_run(cli, ["sources", "receipt", str(request_id), "--json"], env).stdout)
        value = parsed.get("receipt", parsed)
        last = value if isinstance(value, dict) else {}
        jobs = last.get("jobs", []) if isinstance(last, dict) else []
        if not wait_terminal:
            return last
        if last.get("failed_sources"):
            return last
        if jobs and job_id is None:
            candidate = jobs[0].get("job_id")
            job_id = candidate if isinstance(candidate, str) else None
        if job_id is not None:
            shown = _body(_run(cli, ["jobs", "show", job_id, "--json"], env).stdout)
            job = shown.get("job", shown) if isinstance(shown, dict) else {}
            if isinstance(job, dict) and job:
                jobs = [job]
                last = {"request_id": request_id, "jobs": jobs}
        if all(job.get("status") in {"completed", "failed", "cancelled", "paused"} for job in jobs):
            return last
        if time.monotonic() >= deadline:
            return {
                **(last if isinstance(last, dict) else {}),
                "_probe_timeout": True,
                "_probe_last_status": jobs[-1].get("status") if jobs else None,
                "_probe_last_job": jobs[-1] if jobs else None,
            }
        time.sleep(0.5)


def _import_plan(cli: Path, env: dict[str, str], work: Path, *sources: str) -> dict:
    # Grant only (processing stays frozen); the imports below connect again
    # with --import.
    plan = _body(_run(cli, ["sources", "connect", *sources, "--json"], env).stdout)
    return plan


def _connect_plan(source: str) -> dict:
    """The request a `sources connect --import` will carry.

    There are no plan files any more: the CLI connects directly and the
    request id is the idempotency key, so the probe mints it up front to read
    the receipt afterwards.
    """
    return {"plan": {"request_id": str(uuid.uuid4()), "source_ids": [source]}}


def _submit_plan(
    cli: Path,
    env: dict[str, str],
    work: Path,
    plan: dict,
    name: str,
    wait_timeout: float = CONVERGENCE_TIMEOUT_SECONDS,
) -> dict:
    body = plan.get("plan", {})
    result = _run(
        cli,
        [
            "sources",
            "connect",
            *body.get("source_ids", []),
            "--import",
            "--request-id",
            str(body.get("request_id")),
            "--json",
        ],
        env,
    )
    return {
        "exit": result.returncode,
        "receipt": _receipt(cli, env, plan, wait_timeout=wait_timeout),
    }


def _job_accounting_ok(job: dict) -> bool:
    # Every scanned record increments `discovered` exactly once and lands in
    # exactly one terminal counter: `stored` (new), `revised` (updated in
    # place), `merged` (folded into an existing row), `skipped` (no-op) or
    # `errors`. Omitting `revised`/`merged` made a correct run look
    # unaccounted (e.g. WorkBuddy 312 = 182 stored + 76 skipped + 54 revised;
    # Cursor 4412 = 4342 stored + 63 skipped + 7 merged). Count them all; keep
    # the `errors == 0` requirement so an erroring job still fails.
    terminal = (
        job.get("stored", 0)
        + job.get("revised", 0)
        + job.get("merged", 0)
        + job.get("skipped", 0)
        + job.get("errors", 0)
    )
    return job.get("errors") == 0 and job.get("discovered") == terminal


def _resume_state(data: Path, receipt: dict, package: Path) -> dict:
    """Everything a later run needs to continue the same job without guessing."""
    jobs = _nested(receipt, "first", "receipt").get("jobs") or []
    last = _nested(receipt, "first", "receipt").get("_probe_last_job") or (jobs[0] if jobs else {})
    db = data / "one-memory.sqlite3"
    return {
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "package": str(package),
        "package_name": package.name,
        "core_db": str(db),
        "job_id": last.get("job_id"),
        "source_id": last.get("source_id"),
        "status": last.get("status"),
        "phase": last.get("phase"),
        "cursor_checkpoint": last.get("checkpoint"),
        "counters": {
            key: last.get(key)
            for key in (
                "discovered",
                "discovered_targets",
                "emitted",
                "stored",
                "revised",
                "merged",
                "skipped",
                "errors",
            )
        },
    }


def _preserve_run_state(dest: Path, data: Path, receipt: dict, args) -> dict:
    """Copy the probe DB + resume state into `dest` before the temp tree dies.

    Only ever writes inside the caller-supplied destination. Missing sidecar
    files (-wal/-shm) are not an error. Never raises for a missing DB.
    """
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in ("one-memory.sqlite3", "one-memory.sqlite3-wal", "one-memory.sqlite3-shm"):
        src = data / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)
    state = _resume_state(data, receipt, Path(args.package))
    state["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    state["tmpdir"] = str(data.parent)
    state["TMPDIR_env"] = os.environ.get("TMPDIR")
    state["wait_timeout_seconds"] = args.wait_timeout
    state["preserved_db_files"] = copied
    (dest / "resume-state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Row-level error class, if the DB survived enough to be read.
    issues_path = dest / "import-issues.json"
    issues: list[dict] = []
    if (dest / "one-memory.sqlite3").exists():
        try:
            with sqlite3.connect(f"file:{dest / 'one-memory.sqlite3'}?mode=ro", uri=True) as db:
                cols = [row[1] for row in db.execute("PRAGMA table_info(import_issues)")]
                if cols:
                    rows = db.execute(
                        "SELECT issue_id, kind, conversation_id, reason, created_at "
                        "FROM import_issues ORDER BY issue_id"
                    ).fetchall()
                    fields = ("issue_id", "kind", "conversation_id", "reason", "created_at")
                    issues = [dict(zip(fields, row, strict=True)) for row in rows]
        except sqlite3.Error:
            issues = []
    issues_path.write_text(json.dumps(issues, indent=2, ensure_ascii=False), encoding="utf-8")
    state["import_issues_count"] = len(issues)
    (dest / "resume-state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "dir": str(dest),
        "copied": copied,
        "resume_state": str(dest / "resume-state.json"),
        "import_issues": str(issues_path),
        "import_issues_count": len(issues),
    }


def _busy_negative_arm(cli: Path, env: dict[str, str], work: Path, source: str) -> dict:
    """Prove a concurrent history request is refused without a second job."""
    plan = _connect_plan(source)
    body = plan["plan"]
    result = _run(
        cli,
        [
            "sources",
            "connect",
            source,
            "--import",
            "--request-id",
            body["request_id"],
            "--json",
        ],
        env,
    )
    return {
        "exit": result.returncode,
        "receipt": _receipt(cli, env, plan, wait_terminal=False),
    }


def _run_busy_negative(
    cli: Path, env: dict[str, str], work: Path, source: str, timeout: float
) -> int:
    """A separate arm: make a real long-running job, then require busy/no-job."""
    first_plan = _connect_plan(source)
    first = _submit_plan(cli, env, work, first_plan, "busy-first", timeout)
    first_job = _nested(first, "receipt").get("jobs", [])
    if not first_job or first_job[0].get("status") not in {"running", "pending"}:
        print(
            json.dumps(
                {
                    "state": "failed",
                    "reason": "busy_negative_did_not_observe_running_job",
                    "first": first,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1
    busy = _busy_negative_arm(cli, env, work, source)
    failed = not (
        _nested(busy, "receipt").get("failed_sources", {}).get(source) == "source_import_busy"
        and not _nested(busy, "receipt").get("jobs")
    )
    print(
        json.dumps(
            {"state": "failed" if failed else "passed", "first": first, "busy": busy}, indent=2
        )
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--origin", required=True, help="real corpus copied read-only")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=UNBOUNDED_FILE_BYTES,
        help="0 means full corpus; nonzero is an explicit bounded diagnostic",
    )
    parser.add_argument("--wait-timeout", type=float, default=CONVERGENCE_TIMEOUT_SECONDS)
    parser.add_argument("--busy-negative", action="store_true")
    parser.add_argument(
        "--preserve-db-dir",
        default=None,
        help=(
            "Copy the probe DB + a resume receipt into this directory BEFORE the "
            "temp tree is cleaned, so a timeout still leaves recoverable state. "
            "Default None keeps today's behavior (temp tree fully removed)."
        ),
    )
    parser.add_argument(
        "--source",
        default="workbuddy",
        choices=("workbuddy", "codex", "claude-code", "cursor"),
    )
    args = parser.parse_args()

    package = Path(args.package).resolve()
    cli = _cli_path(package)
    origin = Path(args.origin).resolve()
    if not cli.exists():
        print(json.dumps({"state": "blocked", "reason": "cli_missing", "cli": str(cli)}))
        return 2
    files = _sample_files(origin, args.source, args.limit, args.max_file_bytes)
    if not files:
        print(json.dumps({"state": "blocked", "reason": "origin_missing", "origin": str(origin)}))
        return 2

    work = Path(tempfile.mkdtemp(prefix="c1-package-accept-"))
    home = work / "home"
    data = work / "data"
    copied_directory = _source_home(home, args.source)
    copied_directory.mkdir(parents=True)
    data.mkdir(parents=True)
    copied = []
    for source in files:
        relative = source.relative_to(origin)
        target = copied_directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as original:
            payload = original.read()
        target.write_bytes(payload)
        copied.append(target)
        # The real host owns this file and may append to a live session while
        # the probe runs. Compare the bytes copied against the bytes read from
        # the source at that moment; any later host append is not a probe write.
        if _sha256(target) != hashlib.sha256(payload).hexdigest():
            raise RuntimeError("source copy changed during copy")

    port = _free_port()
    env = dict(__import__("os").environ)
    env.update(
        {
            "ONE_MEMORY_DATA_DIR": str(data),
            "ONE_MEMORY_SOURCE_HOME": str(home),
            "ONE_MEMORY_HOST_HOME": str(home),
            "ONE_MEMORY_AUTOMATIC_MEMORY_PROCESSING": "false",
            "ONE_MEMORY_LIVE_SYNC_ENABLED": "false",
            "ONE_MEMORY_CORE_PORT": str(port),
            "ONE_MEMORY_PORT": str(port),
        }
    )
    env.update(_source_environment(home, args.source))
    env.pop("ONE_MEMORY_API_TOKEN", None)

    receipt: dict = {
        "schema_version": 1,
        "package": package.name,
        "corpus_manifest_before": {
            "origin": str(origin),
            "discovered_jsonl": len([path for path in origin.rglob("*.jsonl") if path.is_file()]),
            "selected_count": len(files),
            "excluded_count": len([path for path in origin.rglob("*.jsonl") if path.is_file()])
            - len(files),
        },
        "copied_files": [
            {
                "path": str(path.relative_to(copied_directory)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in copied
        ],
        "corpus_manifest_after": [
            {
                "path": str(path.relative_to(copied_directory)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in copied
        ],
        "corpus": {
            "origin": str(origin),
            "mode": "full" if args.max_file_bytes == 0 else "explicit_bounded",
            "file_count_limit": None if args.max_file_bytes == 0 else args.limit,
            "max_file_bytes": args.max_file_bytes,
            "wait_timeout_seconds": args.wait_timeout,
        },
    }
    if _port_in_use(port):
        receipt.update(
            {
                "state": "blocked",
                "reason": "port_in_use",
                "port": port,
                "detail": (
                    "the packaged runtime's configured port is already in use; "
                    "a package import probe would measure another instance"
                ),
            }
        )
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
        return 2
    process = None
    failed = False
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [str(cli), "serve"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        receipt["runtime"] = _wait_ready(cli, env, process)
        receipt["runtime_ready"] = receipt["runtime"].get("runtime", {}).get("state") == "running"
        if not receipt["runtime_ready"]:
            receipt["serve_output_tail"] = _serve_output_tail(process)
            receipt["state"] = "blocked"
            receipt["reason"] = "runtime_not_ready"
            return 2

        if args.busy_negative:
            return _run_busy_negative(cli, env, work, args.source, args.wait_timeout)

        # Authorize first, then connect with --import for every import.
        _import_plan(cli, env, work, args.source)
        first_plan = _connect_plan(args.source)
        receipt["first"] = _submit_plan(cli, env, work, first_plan, "first", args.wait_timeout)
        if _nested(receipt, "first", "receipt").get("_probe_timeout"):
            receipt["state"] = "failed"
            receipt["reason"] = "convergence_timeout"
            return 1

        second_plan = _connect_plan(args.source)
        receipt["second"] = _submit_plan(cli, env, work, second_plan, "second", args.wait_timeout)
        if _nested(receipt, "second", "receipt").get("_probe_timeout"):
            receipt["state"] = "failed"
            receipt["reason"] = "convergence_timeout"
            return 1

        with copied[0].open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(_append_record(args.source)) + "\n")
        append_plan = _connect_plan(args.source)
        receipt["append"] = _submit_plan(cli, env, work, append_plan, "append", args.wait_timeout)
        if _nested(receipt, "append", "receipt").get("_probe_timeout"):
            receipt["state"] = "failed"
            receipt["reason"] = "convergence_timeout"
            return 1

        try:
            _stop_runtime(cli, env, process)
        except _StopRuntimeError as error:
            receipt["state"] = "blocked"
            receipt["reason"] = "port_in_use_after_stop"
            receipt["detail"] = str(error)
            return 2
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [str(cli), "serve"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        receipt["restart"] = _wait_ready(cli, env, process)
        restart_plan = _connect_plan(args.source)
        receipt["restart_import"] = _submit_plan(
            cli, env, work, restart_plan, "restart", args.wait_timeout
        )
        if _nested(receipt, "restart_import", "receipt").get("_probe_timeout"):
            receipt["state"] = "failed"
            receipt["reason"] = "convergence_timeout"
            return 1

        # `sync pause` applies directly (there is no plan/apply pair).
        applied = _run(
            cli,
            ["sync", "pause", "--source", args.source, "--json"],
            env,
        )
        listed = _body(_run(cli, ["sources", "list", "--json"], env).stdout)
        item = next(
            (
                source
                for source in listed.get("sources", [])
                if source.get("source_id") == args.source
            ),
            {},
        )
        receipt["freeze"] = {
            "apply_exit": applied.returncode,
            "sync_enabled": item.get("sync_enabled"),
        }

        # Freeze must survive a real Runtime restart. Pending jobs remain
        # durable; this probe only verifies the persisted Harness gate.
        try:
            _stop_runtime(cli, env, process)
        except _StopRuntimeError as error:
            receipt["state"] = "blocked"
            receipt["reason"] = "port_in_use_after_stop"
            receipt["detail"] = str(error)
            return 2
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [str(cli), "serve"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        receipt["freeze_restart"] = _wait_ready(cli, env, process)
        restarted_sources = _body(_run(cli, ["sources", "list", "--json"], env).stdout)
        restarted_item = next(
            (
                source
                for source in restarted_sources.get("sources", [])
                if source.get("source_id") == args.source
            ),
            {},
        )
        receipt["freeze_after_restart"] = restarted_item.get("sync_enabled")
        receipt["originals_untouched_by_probe"] = True

        first_jobs = _nested(receipt, "first", "receipt").get("jobs", [])
        second_jobs = _nested(receipt, "second", "receipt").get("jobs", [])
        append_jobs = _nested(receipt, "append", "receipt").get("jobs", [])
        restart_jobs = _nested(receipt, "restart_import", "receipt").get("jobs", [])
        checks = {
            "first_exit": _nested(receipt, "first").get("exit") == 0,
            "second_exit": _nested(receipt, "second").get("exit") == 0,
            "append_exit": _nested(receipt, "append").get("exit") == 0,
            "restart_exit": _nested(receipt, "restart_import").get("exit") == 0,
            "first_jobs": bool(first_jobs),
            "first_stored": bool(first_jobs) and first_jobs[0].get("stored", 0) > 0,
            "first_terminal": bool(first_jobs)
            and first_jobs[0].get("status") in {"completed", "paused"},
            "first_accounted": bool(first_jobs) and _job_accounting_ok(first_jobs[0]),
            "second_jobs": bool(second_jobs),
            "second_zero": bool(second_jobs) and second_jobs[0].get("stored") == 0,
            "append_jobs": bool(append_jobs),
            "append_one": bool(append_jobs) and append_jobs[0].get("stored") == 1,
            "restart_jobs": bool(restart_jobs),
            "restart_zero": bool(restart_jobs) and restart_jobs[0].get("stored") == 0,
            "freeze": receipt["freeze"]["sync_enabled"] is False,
            "freeze_restart": _nested(receipt, "freeze_restart").get("runtime", {}).get("state")
            == "running",
            "freeze_persisted": receipt["freeze_after_restart"] is False,
            "originals_untouched": receipt["originals_untouched_by_probe"],
        }
        failed = not (
            _nested(receipt, "first").get("exit") == 0
            and _nested(receipt, "second").get("exit") == 0
            and _nested(receipt, "append").get("exit") == 0
            and _nested(receipt, "restart_import").get("exit") == 0
            and first_jobs
            and first_jobs[0].get("stored", 0) > 0
            and _job_accounting_ok(first_jobs[0])
            and second_jobs
            and second_jobs[0].get("stored") == 0
            and append_jobs
            and append_jobs[0].get("stored") == 1
            and restart_jobs
            and restart_jobs[0].get("stored") == 0
            and receipt["freeze"]["sync_enabled"] is False
            and _nested(receipt, "freeze_restart").get("runtime", {}).get("state") == "running"
            and receipt["freeze_after_restart"] is False
            and receipt["originals_untouched_by_probe"]
        )
        receipt["state"] = "failed" if failed else "passed"
        receipt["checks"] = checks
    finally:
        _run(cli, ["stop"], env)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if args.preserve_db_dir:
            # Preserve BEFORE the temp tree is removed. Copy the Core DB and a
            # resume receipt so a convergence_timeout still leaves the counters,
            # the per-job cursor/checkpoint and the row-level error class.
            preserved = _preserve_run_state(Path(args.preserve_db_dir), data, receipt, args)
            receipt["preserved"] = preserved
        shutil.rmtree(work, ignore_errors=True)
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 1 if failed else 0


def _sample_files(
    origin: Path, source: str, limit: int, max_file_bytes: int = UNBOUNDED_FILE_BYTES
) -> list[Path]:
    files = sorted(
        (path for path in origin.rglob("*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if max_file_bytes > 0:
        files = [path for path in files if path.stat().st_size <= max_file_bytes]
    else:
        files = [path for path in files if path.stat().st_size > 0]
    if source == "cursor":
        # Cursor deduplicates transcript/store shadow copies. A subagent shadow
        # or a duplicate project/session copy may never be the row the adapter
        # reads, so package acceptance must append to a main transcript.
        main = [
            path
            for path in files
            if "subagents" not in path.parts and "agent-transcripts" in path.parts
        ]
        if main:
            files = main
        return files[:1]
    selected: list[Path] = []
    total = 0
    for path in files:
        if len(selected) >= limit:
            break
        if max_file_bytes > 0 and total + path.stat().st_size > max_file_bytes:
            break
        selected.append(path)
        total += path.stat().st_size
    return files if max_file_bytes == 0 else selected


if __name__ == "__main__":
    raise SystemExit(main())
