"""`sdk-migration.log` explains an unfinished upgrade, and a legacy home is never served."""

import asyncio
import json
import os
import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from pydantic import SecretStr

from one_memory import cli
from one_memory.api import SdkMigrationRequired, create_app
from one_memory.config import Settings
from one_memory.imports.storage import EvidenceStore
from one_memory.runtime_client import RuntimeStatus
from one_memory.sdk_data_plane_migration import DataPlaneMigrationError
from one_memory.sdk_migration_log import describe_error, log_path, write_migration_log
from one_memory.secrets import InMemorySecretStore
from one_memory.target_schema import bootstrap_fresh_management_schema

SECRET = "我的私人记忆 password=hunter2"
MIGRATE = ["sdk-migrate", "--runtime-build", "22", "--yes", "--json"]


def lines(data_dir: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path(data_dir).read_text().splitlines()]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ONE_MEMORY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ONE_MEMORY_MIGRATION_CALLER", "desktop")
    monkeypatch.setenv("ONE_MEMORY_MIGRATION_RUNTIME_BUILD", "22")
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus(state="stopped", code="runtime_not_running"),
    )
    return data_dir


def legacy_home(data_dir: Path) -> Path:
    database = data_dir / "one-memory.sqlite3"
    EvidenceStore(database)
    return database


def test_a_line_never_carries_free_text_or_an_exception_message(tmp_path):
    try:
        try:
            raise ValueError(SECRET)
        except ValueError as cause:
            raise DataPlaneMigrationError("sdk_data_plane_write_job_input_missing") from cause
    except DataPlaneMigrationError as error:
        write_migration_log(
            tmp_path,
            writer="core-sdk-migrate",
            event="migrate.failed",
            outcome="failed",
            code="sdk_migration_preflight_failed",
            error=error,
            details={"note": SECRET, "count": 3, "nested": {"text": SECRET}},
        )
    raw = log_path(tmp_path).read_text()
    assert "hunter2" not in raw and "私人" not in raw
    [line] = lines(tmp_path)
    assert line["details"] == {"note": "<redacted>", "count": 3, "nested": {"text": "<redacted>"}}
    chain = line["error"]["chain"]
    assert [item["type"] for item in chain] == ["DataPlaneMigrationError", "ValueError"]
    assert chain[0]["code"] == "sdk_data_plane_write_job_input_missing"
    assert "code" not in chain[1]
    assert any(
        frame.endswith(":test_a_line_never_carries_free_text_or_an_exception_message")
        for frame in chain[0]["frames"]
    )


def test_logging_never_raises(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the directory should be")
    write_migration_log(tmp_path, writer="core-startup", event="x", outcome="succeeded")


def test_a_failed_migration_records_the_phase_and_the_real_cause(home, monkeypatch):
    legacy_home(home)

    def run_startup_migration(**options):
        options["progress"]("bundle")
        options["progress"]("prepare")
        raise RuntimeError(SECRET)

    import one_memory.sdk_migration_runner as runner

    monkeypatch.setattr(runner, "run_startup_migration", run_startup_migration)
    out = StringIO()
    assert cli.run(MIGRATE, output=out) == 1
    assert json.loads(out.getvalue().splitlines()[-1])["code"] == "sdk_migration_preflight_failed"

    written = lines(home)
    assert "hunter2" not in log_path(home).read_text()
    assert [line["event"] for line in written] == [
        "migrate.invoked",
        "phase.finished",
        "phase.started",
        "phase.finished",
        "phase.started",
        "migrate.failed",
    ]
    assert all(line["writer"] == "core-sdk-migrate" for line in written)
    assert all(line["caller"] == "desktop" and line["runtime_build"] == "22" for line in written)
    assert written[0]["details"]["authority"] == "legacy"
    assert written[1]["details"]["phase"] == "preflight"
    failed = written[-1]
    assert failed["code"] == "sdk_migration_preflight_failed"
    # The generic code says nothing; the phase and the exception chain do.
    assert failed["details"]["phase"] == "prepare"
    assert failed["details"]["code_is_fallback"] is True
    assert failed["error"]["chain"][0]["type"] == "RuntimeError"
    assert any("run_startup_migration" in frame for frame in failed["error"]["chain"][0]["frames"])


def test_a_named_failure_keeps_its_code(home, monkeypatch):
    legacy_home(home)

    def run_startup_migration(**_options):
        raise DataPlaneMigrationError("sdk_migration_model_profile_required")

    import one_memory.sdk_migration_runner as runner

    monkeypatch.setattr(runner, "run_startup_migration", run_startup_migration)
    assert cli.run(MIGRATE, output=StringIO()) == 1
    failed = lines(home)[-1]
    assert failed["code"] == "sdk_migration_model_profile_required"
    assert failed["details"]["phase"] == "preflight"
    assert failed["details"]["code_is_fallback"] is False


def test_a_finished_migration_records_the_result(home, monkeypatch):
    legacy_home(home)
    from one_memory.sdk_migration_runner import StartupMigrationResult

    startup_order = []

    def run_startup_migration(**options):
        startup_order.append("run_migration")
        options["progress"]("cutover")
        return StartupMigrationResult(state="cutover", migration_id="sdk-authority-v2-abc")

    import one_memory.hy_runtime_config as hy_runtime_config
    import one_memory.sdk_migration_runner as runner

    monkeypatch.setattr(
        hy_runtime_config,
        "configure_hy_runtime_environment",
        lambda data_dir: startup_order.append(f"configure_hy:{data_dir}"),
    )
    monkeypatch.setattr(runner, "run_startup_migration", run_startup_migration)
    assert cli.run(MIGRATE, output=StringIO()) == 0
    assert startup_order == [f"configure_hy:{home}", "run_migration"]
    finished = lines(home)[-1]
    assert finished["event"] == "migrate.finished"
    assert finished["migration_id"] == "sdk-authority-v2-abc"
    assert finished["details"]["state"] == "cutover"
    assert finished["details"]["last_phase"] == "cutover"


def test_a_refused_migration_is_logged(home, monkeypatch):
    legacy_home(home)
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus(state="running", code="runtime_running"),
    )
    assert cli.run(MIGRATE, output=StringIO()) == 4
    assert lines(home)[-1]["code"] == "sdk_migration_runtime_running"


def test_the_status_probe_is_logged(home):
    legacy_home(home)
    assert cli.run(["sdk-migration-status", "--json"], output=StringIO()) == 0
    probed = lines(home)[-1]
    assert probed["writer"] == "core-sdk-migration-status"
    assert probed["details"] == {"authority": "legacy", "state": "required"}


def test_serving_core_exits_on_an_unmigrated_home(home):
    legacy_home(home)
    with pytest.raises(SystemExit) as exit_:
        cli._refuse_unmigrated_home(home)
    assert exit_.value.code == cli.SDK_MIGRATION_REQUIRED_EXIT == 78
    refused = lines(home)[-1]
    assert refused["writer"] == "core-startup"
    assert refused["event"] == "core.refused_legacy"
    assert refused["code"] == "sdk_migration_required"
    assert refused["details"]["authority"] == "legacy"


def test_serving_core_starts_on_a_new_or_migrated_home(home):
    cli._refuse_unmigrated_home(home)
    bootstrap_fresh_management_schema(home / "one-memory.sqlite3")
    cli._refuse_unmigrated_home(home)
    assert not log_path(home).exists()


def _settings(data_dir: Path) -> Settings:
    return Settings(
        data_dir=data_dir,
        source_home=data_dir.parent,
        api_token=SecretStr("migration-log-test"),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )


def test_a_serving_app_refuses_a_legacy_home_at_startup(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy_home(data_dir)
    app = create_app(_settings(data_dir), manage_runtime=True, secret_store=InMemorySecretStore())

    async def start() -> None:
        async with app.router.lifespan_context(app):
            pass

    with pytest.raises(SdkMigrationRequired):
        asyncio.run(start())
    refused = [line for line in lines(data_dir) if line["event"] == "core.refused_legacy"]
    assert refused and refused[-1]["details"]["serving"] is True


@pytest.mark.fresh_sdk
def test_startup_records_the_authority_it_read(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(_settings(data_dir), secret_store=InMemorySecretStore())

    async def start() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(start())
    read = [line for line in lines(data_dir) if line["event"] == "core.authority_read"]
    assert read[-1]["details"]["authority"] == "sdk"
    assert read[-1]["writer"] == "core-startup"


def test_describe_error_keeps_only_the_file_name_of_a_path(tmp_path):
    error = OSError(28, SECRET, str(tmp_path / "private-name" / "one-memory.sqlite3"))
    described = describe_error(error)
    [item] = described["chain"]
    assert item["errno"] == 28
    assert item["file"] == "one-memory.sqlite3"
    assert "private-name" not in json.dumps(described)


def test_the_log_is_private(tmp_path):
    write_migration_log(tmp_path, writer="core-startup", event="x", outcome="succeeded")
    mode = os.stat(log_path(tmp_path)).st_mode & 0o777
    assert mode == 0o600
    assert sqlite3  # the module under test reads migration rows with sqlite3


def _migrated_schema_home(data_dir: Path) -> Path:
    from one_memory.database_access import close_main_databases
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    database = data_dir / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    close_main_databases()
    return database


def test_the_runner_names_the_step_and_sub_step_that_failed(tmp_path, monkeypatch):
    """`sdk_migration_preflight_failed` alone said nothing; the step lines do."""
    import one_memory.sdk_migration_runner as runner

    _migrated_schema_home(tmp_path)

    def broken_queue(_database_path):
        raise ValueError(SECRET)

    monkeypatch.setattr(runner, "prepare_legacy_write_queue_for_cutover", broken_queue)
    with pytest.raises(ValueError):
        runner.run_startup_migration(
            data_dir=tmp_path,
            runtime_build="22",
            sdk_version="2.0.5",
            namespace="governed-v2",
        )
    assert cli.migration_failure_code(ValueError(SECRET)) == "sdk_migration_preflight_failed"
    raw = log_path(tmp_path).read_text()
    assert "hunter2" not in raw and "私人" not in raw
    written = lines(tmp_path)
    steps = [
        (line["event"], line["details"]["step"])
        for line in written
        if "step" in line.get("details", {})
    ]
    assert steps[:8] == [
        ("step.finished", "preflight.authority_read"),
        ("step.finished", "preflight.profile_read"),
        ("step.finished", "preflight.resumable_identity"),
        ("step.finished", "preflight.legacy_ids_scan"),
        ("step.finished", "preflight.model_profile_check"),
        ("step.finished", "prepare.recover_abandoned_staging"),
        ("step.finished", "bundle.ensure"),
        ("step.finished", "bundle.restore_point"),
    ]
    assert steps[-1] == ("step.failed", "prepare.legacy_work")
    # The failure put the home back; the restore says so after the failed step.
    assert [line["event"] for line in written[-2:]] == ["restore.started", "restore.finished"]
    failed = next(line for line in reversed(written) if line["event"] == "step.failed")
    assert failed["migration_id"].startswith("sdk-authority-v2-")
    assert failed["details"]["sub_step"] == "write_queue"
    assert failed["error"]["chain"][0]["type"] == "ValueError"
    assert any("broken_queue" in frame for frame in failed["error"]["chain"][0]["frames"])
    # The ledger transition before it is there too, with the phase it left.
    started = next(line for line in written if line["event"] == "ledger.started")
    assert started["details"] == {"created": True, "state": "running", "phase": "preflight"}


def test_a_coded_failure_with_a_row_id_keeps_both_name_and_id(tmp_path):
    error = DataPlaneMigrationError("sdk_data_plane_write_job_input_missing:job-0193")
    [item] = describe_error(error)["chain"]
    assert item["code"] == "sdk_data_plane_write_job_input_missing"
    assert item["subject"] == "job-0193"
    # The dialog gets the specific name instead of the generic fallback.
    assert cli.migration_failure_code(error) == "sdk_data_plane_write_job_input_missing"


def test_a_second_migrator_waits_for_the_first(home, monkeypatch):
    """The Runtime service and Desktop can both migrate at login; never at once."""
    import threading

    import one_memory.sdk_migration_runner as runner
    from one_memory.sdk_migration_runner import StartupMigrationResult

    legacy_home(home)
    monkeypatch.setattr(
        runner,
        "run_startup_migration",
        lambda **_options: StartupMigrationResult(state="not_required"),
    )
    held = threading.Event()
    release = threading.Event()

    def first() -> None:
        with cli._migration_lock(home):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=first)
    holder.start()
    held.wait(5)
    results: list[int] = []
    second = threading.Thread(target=lambda: results.append(cli.run(MIGRATE, output=StringIO())))
    second.start()
    for _ in range(100):
        if log_path(home).exists() and any(
            line["event"] == "migrate.waiting_for_lock" for line in lines(home)
        ):
            break
        threading.Event().wait(0.02)
    assert results == []
    release.set()
    holder.join(5)
    second.join(5)
    assert results == [0]
    events = [line["event"] for line in lines(home)]
    assert events.index("migrate.waiting_for_lock") < events.index("migrate.lock_acquired")
