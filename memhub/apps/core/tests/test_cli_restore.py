import asyncio
import io
import json
import sqlite3

from one_memory import cli
from one_memory.config import Settings
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.runtime_client import RuntimeStatus
from one_memory.sdk_data_plane_migration import SdkDataPlaneMigrationStore


def test_restore_requires_a_source(tmp_path, monkeypatch) -> None:
    output = io.StringIO()
    assert cli.run(["restore"], output=output) == 2
    assert "--source" in output.getvalue()


def test_restore_refuses_while_the_runtime_is_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("running", "runtime_verified"),
    )
    output = io.StringIO()

    code = cli.run(
        ["restore", "--source", "backups/x.sqlite3", "--yes", "--json"],
        output=output,
    )
    assert code == 4
    assert "backup_restore_runtime_running" in output.getvalue()


def test_restore_previews_without_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()

    assert cli.run(["restore", "--source", "backups/x.sqlite3", "--json"], output=output) == 4
    assert '"state": "preview"' in output.getvalue() or '"state":"preview"' in output.getvalue()


def test_restore_success_returns_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )

    class Result:
        restored_bytes = 123
        previous_backup_path = tmp_path / "backups" / "pre.sqlite3"
        source_schema_version = 36

    monkeypatch.setattr(
        "one_memory.managed_restore.restore_managed_backup",
        lambda **_kwargs: Result(),
    )
    output = io.StringIO()

    code = cli.run(
        ["restore", "--source", "backups/x.sqlite3", "--yes", "--json"],
        output=output,
    )
    assert code == 0
    assert '"state":"restored"' in output.getvalue().replace(" ", "")


def test_restore_sdk_bundle_uses_full_data_plane_restore(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )

    class Result:
        bundle_id = "migration-1"
        restored_files = 4
        restored_bytes = 123
        previous_database_path = tmp_path / "backups" / "pre.sqlite3"
        previous_sdk_root = tmp_path / "backups" / "pre.sdk-data"

    def restore(**_kwargs):
        return Result()

    monkeypatch.setattr(
        "one_memory.sdk_migration_bundle.restore_sdk_migration_bundle",
        restore,
    )
    output = io.StringIO()

    code = cli.run(
        [
            "restore",
            "--source",
            "backups/migration-1.sdk-bundle",
            "--yes",
            "--json",
        ],
        output=output,
    )
    assert code == 0
    document = output.getvalue().replace(" ", "")
    assert '"bundle_id":"migration-1"' in document
    assert '"restored_files":4' in document


def _stopped(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )


def _legacy_home(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO evidence ("
            "evidence_id,idempotency_key,user_id,source_id,conversation_id,"
            "content_kind,role,written_by,verbatim,content_hash,revision,"
            "imported_at,updated_at"
            ") VALUES ('e1','k','local','cursor','s','raw_conversation',"
            "'user','person','hello','hash',1,'now','now')"
        )


def test_sdk_migrate_never_runs_while_runtime_is_live(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("running", "healthy"),
    )
    output = io.StringIO()
    code = cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=output)
    assert code == 4
    assert json.loads(output.getvalue())["code"] == "sdk_migration_runtime_running"


def test_sdk_migrate_previews_without_confirmation(tmp_path, monkeypatch) -> None:
    _stopped(monkeypatch, tmp_path)
    _legacy_home(tmp_path)
    output = io.StringIO()
    assert cli.run(["sdk-migrate", "--runtime-build", "20", "--json"], output=output) == 4
    assert json.loads(output.getvalue())["state"] == "preview"
    assert SdkDataPlaneMigrationStore(tmp_path / "one-memory.sqlite3").authority().value == (
        "legacy"
    )


def test_sdk_migrate_requires_the_runtime_build(tmp_path, monkeypatch) -> None:
    _stopped(monkeypatch, tmp_path)
    assert cli.run(["sdk-migrate", "--yes"], output=io.StringIO()) == 2


def test_sdk_migrate_reports_each_step_then_the_result(tmp_path, monkeypatch) -> None:
    _stopped(monkeypatch, tmp_path)
    _legacy_home(tmp_path)
    output = io.StringIO()

    code = cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=output)

    assert code == 0, output.getvalue()
    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [line["step"] for line in lines[:-1]] == ["bundle", "prepare", "cutover", "cleanup"]
    assert lines[-1]["state"] == "cutover"
    assert lines[-1]["bundle_id"] == f"{lines[-1]['migration_id']}-pre-cutover"
    assert not (tmp_path / ".sdk-migration-capture-fence.json").exists()

    again = io.StringIO()
    assert cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=again) == 0
    assert json.loads(again.getvalue())["state"] == "not_required"


def test_sdk_migrate_failure_is_one_named_code(tmp_path, monkeypatch) -> None:
    import errno

    _stopped(monkeypatch, tmp_path)

    def full_disk(**_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("one_memory.sdk_migration_runner.run_startup_migration", full_disk)
    output = io.StringIO()
    assert cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=output) == 1
    assert json.loads(output.getvalue()) == {
        "schema_version": 1,
        "state": "failed",
        "code": "sdk_migration_disk_space_insufficient",
    }


def test_sdk_migration_status_says_whether_migrate_has_work(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))

    def status() -> dict[str, object]:
        output = io.StringIO()
        assert cli.run(["sdk-migration-status", "--json"], output=output) == 0
        return json.loads(output.getvalue())

    assert status()["state"] == "not_required"
    _legacy_home(tmp_path)
    assert status() == {"schema_version": 1, "authority": "legacy", "state": "required"}
