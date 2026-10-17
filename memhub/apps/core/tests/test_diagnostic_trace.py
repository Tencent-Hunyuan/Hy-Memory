import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.diagnostic_log import configure_diagnostics, reset_diagnostics
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.secrets import InMemorySecretStore
from tests.source_consent import authorized_request, thaw_source


@pytest.fixture(autouse=True)
def _diagnostics_on(monkeypatch):
    """This module is about what lands in the log, so the log is on.

    Diagnostics are off unless asked; a test that reads the log has to ask.
    """
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")


def test_mutating_request_gets_trace_and_never_logs_path_or_body(tmp_path):
    secret = "private-request-content-918273"
    receipt = configure_diagnostics(
        tmp_path,
        session_id="11111111-1111-4111-8111-111111111111",
    )
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/{secret}", json={"content": secret})
            assert response.status_code == 404
            assert response.headers["X-One-Memory-Trace-Id"]

    try:
        asyncio.run(scenario())
    finally:
        reset_diagnostics()

    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "api.request.started",
        "api.request.completed",
    ]
    assert events[0]["trace_id"] == events[1]["trace_id"]
    assert secret not in receipt.path.read_text()


def test_recall_request_log_includes_safe_host_source_id(tmp_path):
    receipt = configure_diagnostics(tmp_path)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/recall-missing",
                headers={"X-One-Memory-Host-Id": "cursor"},
                json={},
            )
            assert response.status_code == 404

    try:
        asyncio.run(scenario())
    finally:
        reset_diagnostics()
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "recall.request.started",
        "recall.request.completed",
    ]
    assert all(event["details"]["source_id"] == "cursor" for event in events)


IMPORT_SECRET = "private-import-turn-918273"
IMPORT_TRACE = "22222222-2222-4222-8222-222222222222"


def _run_codex_import(tmp_path) -> tuple[str, str]:
    secret = IMPORT_SECRET
    home = tmp_path / "home"
    source = home / ".codex" / "sessions"
    source.mkdir(parents=True)
    (source / "rollout.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "timestamp": datetime(2026, 9, 24, tzinfo=UTC).isoformat(),
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": secret}],
                },
            }
        )
        + "\n"
    )
    data = tmp_path / "data"
    data.mkdir()
    receipt = configure_diagnostics(
        data,
        session_id="11111111-1111-4111-8111-111111111111",
    )
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=data,
            source_home=home,
            api_token=SecretStr("test-token"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={"codex": build_source_catalog(home=home, environment={})["codex"]},
        secret_store=InMemorySecretStore(),
    )
    trace = IMPORT_TRACE
    job_ids: list[str] = []

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={
                    "Authorization": "Bearer test-token",
                    "X-One-Memory-Trace-Id": trace,
                },
            ) as client,
        ):
            authorized = await client.post(
                "/api/v1/sources/codex/authorize",
                json=await authorized_request(client, "codex"),
            )
            authorized.raise_for_status()
            await thaw_source(client, "codex", authorized.json())
            started = await client.post("/api/v1/import-jobs", json={"source_id": "codex"})
            started.raise_for_status()
            job = await app.state.import_supervisor.wait(started.json()["job_id"])
            assert job.status == "completed"
            job_ids.append(job.job_id)

    try:
        asyncio.run(scenario())
    finally:
        reset_diagnostics()
    return receipt.path.read_text(), job_ids[0]


def test_import_request_trace_reaches_the_background_job_without_original_text(tmp_path):
    secret = IMPORT_SECRET
    written, _job_id = _run_codex_import(tmp_path)
    events = [
        event
        for event in map(json.loads, written.splitlines())
        if event.get("trace_id") == IMPORT_TRACE
    ]
    names = [event["event"] for event in events]
    assert "import.request.started" in names
    assert "import.queued" in names
    assert "import.started" in names
    assert "import.finished" in names
    finished = next(event for event in events if event["event"] == "import.finished")
    assert {
        "stored_count",
        "revised_count",
        "skipped_count",
        "error_count",
    } <= finished["details"].keys()
    assert secret not in written


def test_import_run_logs_parse_stages_and_core_resources_without_content(tmp_path):
    written, job_id = _run_codex_import(tmp_path)
    events = list(map(json.loads, written.splitlines()))
    job_events = [event for event in events if event.get("job_id") == job_id]
    names = [event["event"] for event in job_events]
    assert names.index("import.parse.started") < names.index("import.parse.completed")
    assert "import.phase_changed" in names
    completed = next(event for event in job_events if event["event"] == "import.parse.completed")
    assert completed["outcome"] == "succeeded"
    assert {"stored_count", "skipped_count", "error_count"} <= completed["details"].keys()
    everything = [event["event"] for event in events]
    assert "core.resource_snapshot" in everything
    assert everything.index("core.ready") < everything.index("core.stopped")
    assert IMPORT_SECRET not in written


def test_a_failing_import_diagnostic_read_does_not_change_the_runner_result(tmp_path, monkeypatch):
    from one_memory.imports.adapters import ScannedEvidence
    from one_memory.imports.jobs import ImportJobStore, ImportStatus
    from one_memory.imports.runner import ImportRunner
    from tests.test_import_runner import FixtureAdapter, cursor, evidence, stores

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    records, jobs = stores(tmp_path)
    job = jobs.create(user_id="local", source_id="fixture")
    monkeypatch.setattr(ImportJobStore, "skip_reason_counts", locked)
    configure_diagnostics(tmp_path / "logs")
    try:
        result = asyncio.run(
            ImportRunner(evidence_store=records, job_store=jobs).run(
                adapter=FixtureAdapter(
                    [ScannedEvidence(evidence=evidence("m1"), cursor=cursor("line:1"))]
                ),
                job_id=job.job_id,
            )
        )
    finally:
        reset_diagnostics()
    assert result.status is ImportStatus.COMPLETED


def test_a_failing_import_finished_log_does_not_escape_the_supervisor_task(tmp_path, monkeypatch):
    from one_memory.imports import supervisor

    real_event = supervisor.diagnostic_event

    def rejecting(**values):
        if values.get("event") == "import.finished":
            raise ValueError("diagnostic_identifier_invalid")
        real_event(**values)

    monkeypatch.setattr(supervisor, "diagnostic_event", rejecting)
    written, _job_id = _run_codex_import(tmp_path)
    assert "import.started" in written
