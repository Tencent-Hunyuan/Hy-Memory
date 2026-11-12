from __future__ import annotations

import asyncio
import json
import socket

import pytest

from tests.measure_c1_recall_during_import import _run

pytestmark = [pytest.mark.performance, pytest.mark.serial]


def test_workbuddy_recall_remains_available_during_100k_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    # `_run` replaces these process globals to prove the test cannot reach the
    # network; register their originals so pytest restores them afterwards.
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)

    result = asyncio.run(_run(tmp_path, 100_000, 40, 0.01, "workbuddy"))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    assert result["import_status"] == "completed"
    assert result["stored"] == 100_000
    assert result["evidence_rows"] == 100_000
    assert result["hook_accepted"] is True
    assert result["hook_visible_ms"] < 10_000
    assert result["recall_sampling_terminal"] is True
    assert result["during_unavailable"] == 0
    assert result["during_import"]["p95_ms"] < 1_000
    assert result["database_locked_events"] == 0
    assert result["realtime_error_events"] == {
        "import.heartbeat_failed": 0,
        "live_sync.hook_failed": 0,
        "live_sync.internal_error": 0,
    }
