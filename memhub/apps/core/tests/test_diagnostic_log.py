import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from one_memory import diagnostic_log
from one_memory.diagnostic_log import (
    DiagnosticLog,
    bind_trace_id,
    configure_diagnostics,
    current_trace_id,
    database_error_code,
    diagnostic_event,
    diagnostic_exception,
    diagnostics_enabled,
    diagnostics_requested,
    read_trace_events,
    record_database_error,
    reset_diagnostics,
    reset_trace_id,
    resource_snapshot,
)
from one_memory.exception_reporting import report_exception


def test_debug_events_are_collected_only_at_debug_level(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    receipt = configure_diagnostics(tmp_path)
    try:
        diagnostic_event(
            level="debug",
            component="core.poll",
            event="poll.completed",
            outcome="succeeded",
        )
        diagnostic_event(
            level="info",
            component="memory.task",
            event="memory_task.finished",
            outcome="completed",
        )
    finally:
        reset_diagnostics()
        root.setLevel(previous)
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["event"] for event in events] == ["memory_task.finished"]


def test_high_frequency_success_events_are_summarized_but_warnings_are_not(
    tmp_path, monkeypatch
) -> None:
    now = 10.0
    monkeypatch.setattr(diagnostic_log, "monotonic", lambda: now)
    log = DiagnosticLog(tmp_path)
    values = dict(
        component="import.parser",
        event="import.parse.started",
        outcome="started",
        details={"source_id": "cursor", "operation_kind": "sync"},
    )
    for _ in range(100):
        log.write(level="info", **values)
    log.write(level="warn", **values)
    now = 311.0
    log.write(level="info", duration_ms=12, **values)
    assert log.flush()
    events = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert len(events) == 3
    assert events[1]["level"] == "warn"
    assert events[2]["details"]["count"] == 100
    assert events[2]["details"]["summed_ms"] == 12


def test_structured_log_is_private_correlated_and_owner_only(tmp_path):
    log = DiagnosticLog(
        tmp_path,
        session_id="11111111-1111-4111-8111-111111111111",
    )
    trace, token = bind_trace_id("22222222-2222-4222-8222-222222222222")
    try:
        assert current_trace_id() == trace
        log.write(
            level="info",
            component="import.runner",
            event="import.completed",
            outcome="succeeded",
            job_id="job-1",
            duration_ms=42,
            details={"source_id": "codex", "count": 3},
        )
    finally:
        reset_trace_id(token)
    # 写入交给专属线程，调用方不再等磁盘；要读文件就得先排空。
    assert log.flush()
    value = json.loads(log.path.read_text())
    assert value["trace_id"] == trace
    assert value["job_id"] == "job-1"
    assert value["details"] == {"source_id": "codex", "count": 3}
    if os.name != "nt":
        assert log.path.stat().st_mode & 0o777 == 0o600


def test_arbitrary_valid_detail_keys_and_scalars_are_recorded(tmp_path):
    log = DiagnosticLog(tmp_path)
    details = {
        "Custom.metric-v1": "ready",
        "noneValue": None,
        "boolValue": False,
        "intValue": 7,
        "floatValue": 1.5,
    }
    log.write(
        level="info",
        component="core.lifecycle",
        event="core.started",
        outcome="succeeded",
        details=details,
    )
    assert log.flush()
    assert json.loads(log.path.read_text())["details"] == details


@pytest.mark.parametrize(
    "key",
    ["__proto__", "prototype", "constructor", "_private", "9startsWithDigit", "a" * 65],
)
def test_dangerous_or_malformed_detail_keys_are_rejected(tmp_path, key):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_key_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={key: "value"},
        )


@pytest.mark.parametrize("value", [{}, [], object()])
def test_complex_detail_values_are_rejected(tmp_path, value):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_value_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"payload": value},
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_detail_values_are_rejected(tmp_path, value):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_value_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"metric": value},
        )


@pytest.mark.parametrize("value", [2**53, -(2**53), float(2**53), -float(2**53)])
def test_numeric_detail_values_outside_json_safe_range_are_rejected(tmp_path, value):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_value_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"count": value},
        )


@pytest.mark.parametrize("value", ["", "nul\0byte", "carriage\rreturn", "line\nfeed", "x" * 257])
def test_invalid_detail_strings_are_rejected(tmp_path, value):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_value_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"message": value},
        )


def test_more_than_32_detail_keys_are_rejected(tmp_path):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_count_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={f"key{index}": index for index in range(33)},
        )


def test_absolute_or_credential_bearing_stack_is_rejected(tmp_path):
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_stack_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            safe_stack=("/Users/me/private.py:1",),
        )
    with pytest.raises(ValueError, match="diagnostic_stack_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            safe_stack=("one_memory/sk-live-secret-token.py:1:call",),
        )


def test_safe_stack_uses_strict_shared_grammar_and_sixteen_line_limit(tmp_path):
    log = DiagnosticLog(tmp_path)
    for line in (
        "arbitrary stack text",
        "thread=worker|threading.py:12:wait",
        "FILE:///Users/me/private.py:1:call",
        "one_memory/worker.py:9:call /Users/private",
        "one_memory/GOCSPX-privateCredential.py:9:call",
        "packages/ASIA1234567890ABCDEF/file.ts:1:1",
        "packages/Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8/file.ts:1:1",
    ):
        with pytest.raises(ValueError, match="diagnostic_stack_invalid"):
            log.write(
                level="error",
                component="hy.bridge",
                event="hy.failed",
                outcome="failed",
                safe_stack=(line,),
            )

    accepted = (
        "apps/desktop/src/main.ts:80:1",
        "one_memory/diagnostic_log.py:895:_safe_stack_line",
        "threading.py:1045:_bootstrap_inner",
        "thread|threading.py:1045:_bootstrap_inner > one_memory/worker.py:9:call",
    )
    log.write(
        level="error",
        component="hy.bridge",
        event="hy.failed",
        outcome="failed",
        safe_stack=accepted,
    )
    with pytest.raises(ValueError, match="diagnostic_stack_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            safe_stack=tuple(f"one_memory/worker.py:{index + 1}:call" for index in range(17)),
        )
    assert log.flush()
    assert json.loads(log.path.read_text())["safe_stack"] == list(accepted)


def test_invalid_level_is_strict_but_diagnostic_event_remains_fail_open(
    tmp_path, monkeypatch
) -> None:
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_level_invalid"):
        log.write(
            level="fatal",  # type: ignore[arg-type]
            component="core.lifecycle",
            event="core.failed",
            outcome="failed",
        )

    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path / "fail-open")
    try:
        diagnostic_event(
            level="fatal",
            component="core.lifecycle",
            event="core.failed",
            outcome="failed",
        )
    finally:
        reset_diagnostics()
    assert not receipt.path.exists()


def test_a_rejected_payload_does_not_break_the_caller_or_later_events(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path)
    try:
        diagnostic_event(
            level="info",
            component="memory.sedimentation",
            event="memory_sedimentation.toggled",
            outcome="succeeded",
            details={"payload": ["not", "scalar"]},
        )
        diagnostic_event(
            level="info",
            component="memory.sedimentation",
            event="memory_sedimentation.toggled",
            outcome="succeeded",
            details={"custom.metric": 1},
        )
    finally:
        reset_diagnostics()
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["details"] for event in events] == [{"custom.metric": 1}]


def test_trace_projection_does_not_expose_arbitrary_local_detail_keys(tmp_path):
    log = DiagnosticLog(tmp_path)
    log.write(
        level="info",
        component="core.api",
        event="memory.request.completed",
        outcome="succeeded",
        request_id="request-1",
        details={
            "phase": "recall",
            "reason": "/Users/private/query.txt",
            "status": "sk-live-secret-token",
            "custom.private": "local-only",
        },
    )
    assert log.flush()
    events = read_trace_events(tmp_path, request_id="request-1")
    assert events[0]["details"] == {"phase": "recall"}


def test_trace_projection_rejects_symlinked_directory_and_redacts_top_level_secrets(
    tmp_path,
) -> None:
    request_id = "request-1"
    logs = tmp_path / "logs"
    logs.mkdir()
    event = {
        "timestamp": "2026-10-23T01:00:00Z",
        "request_id": request_id,
        "component": "secret.token",
        "event": "credential.failed",
        "outcome": "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
        "error_code": "GOCSPX-clientSecret_A1",
        "error_type": "ASIA1234567890ABCDEF",
        "details": {
            "phase": "recall",
            "reason": "x_ASIA1234567890ABCDEF",
            "mode": "x_GOCSPX-clientSecret_A1",
            "status": "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
            "failure_site": "AIzaSyExampleCredential1234567890",
            "count": 1,
        },
    }
    (logs / "diagnostic-core.jsonl").write_text(json.dumps(event) + "\n")
    projected = read_trace_events(tmp_path, request_id=request_id)
    assert projected == (
        {
            "timestamp": "2026-10-23T01:00:00Z",
            "component": "diagnostic.redacted",
            "event": "diagnostic.redacted",
            "outcome": "redacted",
            "details": {"phase": "recall", "count": 1},
        },
    )

    external = tmp_path / "external"
    external.mkdir()
    (logs / "diagnostic-core.jsonl").unlink()
    logs.rmdir()
    logs.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="diagnostic_directory_unsafe"):
        read_trace_events(tmp_path, request_id=request_id)


def test_credential_detection_is_conservative_and_matches_export_rules() -> None:
    for value in (
        "ASIA1234567890ABCDEF",
        "GOCSPX-clientSecret_A1",
        "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8",
        "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8_",
    ):
        assert diagnostic_log._contains_credential_material(value)
    for value in (
        "11111111-1111-4111-8111-111111111111",
        "abcdef1234567890abcdef1234567890abcdef12",
        "cursor",
    ):
        assert not diagnostic_log._contains_credential_material(value)


def test_trace_reader_never_follows_a_diagnostic_file_symlink(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        json.dumps(
            {
                "timestamp": "2026-10-23T01:00:00Z",
                "request_id": "request-1",
                "component": "core.api",
                "event": "memory.request.completed",
                "outcome": "succeeded",
            }
        )
        + "\n"
    )
    (logs / "diagnostic-core.jsonl").symlink_to(outside)
    assert read_trace_events(tmp_path, request_id="request-1") == ()


def test_the_sedimentation_toggle_payload_is_recorded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path)
    try:
        diagnostic_event(
            level="info",
            component="memory.sedimentation",
            event="memory_sedimentation.toggled",
            outcome="succeeded",
            details={"enabled": False},
        )
    finally:
        reset_diagnostics()
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["event"] for event in events] == ["memory_sedimentation.toggled"]
    assert events[0]["details"]["enabled"] is False


def test_exception_diagnostic_keeps_code_location_not_message():
    secret = "private-provider-response-918273"
    log = DiagnosticLog(Path.cwd())
    try:
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"prompt": f"{secret}\n"},
        )
    except ValueError as error:
        safe = diagnostic_exception(error)
    assert safe["error_type"] == "ValueError"
    assert "one_memory/diagnostic_log.py" in json.dumps(safe)
    assert secret not in json.dumps(safe)
    assert str(__file__) not in json.dumps(safe)


def test_report_exception_never_writes_the_exception_message_to_diagnostics(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path)
    secret = "sk-live-private-provider-response"
    try:
        report_exception(
            "core.test",
            "core.test.failed",
            RuntimeError(f"provider failed with {secret} at /Users/private/query.txt"),
        )
    finally:
        reset_diagnostics()
    payload = receipt.path.read_text()
    assert secret not in payload
    assert "/Users/private" not in payload
    event = json.loads(payload)
    assert event["details"] == {"count": 1}
    assert event["error_type"] == "RuntimeError"


def test_old_logs_are_removed_but_symlinks_are_never_followed(tmp_path):
    log = DiagnosticLog(tmp_path)
    log.path.parent.mkdir(parents=True)
    log.path.write_text("{}\n")
    old = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(log.path, (old, old))
    outside = tmp_path / "outside"
    outside.write_text("private")
    (log.path.parent / "diagnostic-runtime.jsonl").symlink_to(outside)
    log.write(
        level="info",
        component="core.lifecycle",
        event="core.started",
        outcome="succeeded",
    )
    assert log.flush()
    assert outside.read_text() == "private"
    assert json.loads(log.path.read_text())["event"] == "core.started"


def test_total_budget_removes_oldest_known_log(tmp_path, monkeypatch):
    import one_memory.diagnostic_log as module

    log = DiagnosticLog(tmp_path)
    log.path.parent.mkdir(parents=True)
    old = log.path.parent / "diagnostic-runtime.jsonl"
    recent = log.path.parent / "diagnostic-desktop.jsonl"
    old.write_text("12345678")
    recent.write_text("abcdefgh")
    now = datetime.now(UTC).timestamp()
    os.utime(old, (now - 2, now - 2))
    os.utime(recent, (now - 1, now - 1))
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 8)
    log._prune()
    assert not old.exists()
    assert recent.exists()


def test_resource_snapshot_contains_only_numeric_process_counters(tmp_path):
    database = tmp_path / "private-marker" / "one-memory.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        snapshot = resource_snapshot()
        assert snapshot["open_file_count"] >= 0
        assert "database_connection_count" not in snapshot
        assert snapshot["memory_bytes"] >= 0
        assert snapshot["thread_count"] >= 1
        assert str(tmp_path) not in json.dumps(snapshot)
        connection.execute("SELECT 1")


def test_database_errors_are_classified_counted_and_never_log_messages(tmp_path, monkeypatch):
    # Diagnostics are off unless asked, so a test that reads the log has to ask.
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path)
    secret = "/Users/private/marker.sqlite3"
    before = resource_snapshot()["database_error_count"]
    try:
        error = sqlite3.OperationalError(f"database is locked: {secret}")
        assert database_error_code(error) == "database_locked"
        record_database_error("memory.task", error)
        record_database_error("memory.task", error)
    finally:
        reset_diagnostics()
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert events[-1]["error_code"] == "database_locked"
    assert events[-1]["details"]["phase"] == "memory.task"
    assert resource_snapshot()["database_error_count"] == before + 2
    assert secret not in receipt.path.read_text()


def test_database_error_records_safe_route_and_sqlite_code(tmp_path, monkeypatch):
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    receipt = configure_diagnostics(tmp_path)
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    error.sqlite_errorname = "SQLITE_BUSY"
    try:
        record_database_error("core.api", error, route_path="/api/v2/context/search")
    finally:
        reset_diagnostics()
    payload = json.loads(receipt.path.read_text().splitlines()[-1])
    assert payload["details"]["route_path"] == "/api/v2/context/search"
    assert payload["details"]["sqlite_errorcode"] == sqlite3.SQLITE_BUSY
    assert payload["details"]["sqlite_errorname"] == "SQLITE_BUSY"


def test_diagnostics_are_off_unless_asked_for(tmp_path, monkeypatch) -> None:
    """The pipeline log is a debugging instrument, not a product asset.

    Left on it grows without bound — measured at roughly 24 MB across ten
    files in ordinary use — and every event is a write the product does not
    need. So it is off unless asked.
    """
    monkeypatch.delenv("ONE_MEMORY_DIAGNOSTICS", raising=False)
    assert diagnostics_requested() is False
    configure_diagnostics(tmp_path, session_id="off-1")
    assert diagnostics_enabled() is False
    # Off must mean no file at all, not an empty file that keeps growing.
    assert list(tmp_path.iterdir()) == []


def test_diagnostics_turn_on_when_asked(tmp_path, monkeypatch) -> None:
    """`ONE_MEMORY_DIAGNOSTICS=1` gives the trace an Agent can query."""
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    assert diagnostics_requested() is True
    configure_diagnostics(tmp_path, session_id="on-1")
    try:
        assert diagnostics_enabled() is True
    finally:
        reset_diagnostics()


def test_events_carry_the_package_identity_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_BUILD_ID", "One-Memory-H88-4760f30-candidate")
    monkeypatch.setenv("ONE_MEMORY_PACKAGE_ID", "One-Memory-H88-4760f30-candidate")
    monkeypatch.setenv("ONE_MEMORY_ROUND", "88")
    log = DiagnosticLog(tmp_path, session_id="55555555-5555-4555-8555-555555555555")
    log.write(
        level="info",
        component="core.api",
        event="memory.request.completed",
        outcome="succeeded",
    )
    assert log.flush()
    event = json.loads(log.path.read_text())
    # One chain can only be attributed to a package if the legs carry the id.
    assert event["build_id"] == "One-Memory-H88-4760f30-candidate"
    assert event["package_id"] == "One-Memory-H88-4760f30-candidate"
    assert event["round"] == "88"


def test_events_carry_no_identity_for_a_source_run(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ONE_MEMORY_BUILD_ID", raising=False)
    monkeypatch.delenv("ONE_MEMORY_PACKAGE_ID", raising=False)
    monkeypatch.delenv("ONE_MEMORY_ROUND", raising=False)
    log = DiagnosticLog(tmp_path, session_id="66666666-6666-4666-8666-666666666666")
    log.write(
        level="info",
        component="core.api",
        event="memory.request.completed",
        outcome="succeeded",
    )
    assert log.flush()
    event = json.loads(log.path.read_text())
    assert "build_id" not in event
    assert "package_id" not in event
    assert "round" not in event


def test_a_plain_zero_does_not_enable_diagnostics(monkeypatch) -> None:
    """Only the affirmative spellings turn it on.

    Any non-empty value being "true" is how a misconfigured environment
    silently re-enables a log that was deliberately turned off.
    """
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", value)
        assert diagnostics_requested() is False, value
