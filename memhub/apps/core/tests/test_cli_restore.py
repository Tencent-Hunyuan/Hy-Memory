import io

from one_memory import cli
from one_memory.config import Settings
from one_memory.runtime_client import RuntimeStatus


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


def test_migration_bundle_refuses_while_runtime_is_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("running", "runtime_verified"),
    )
    output = io.StringIO()

    code = cli.run(
        [
            "migration-bundle",
            "--bundle-id",
            "migration-1",
            "--runtime-build",
            "build-13",
            "--source-sdk-version",
            "2.0.4",
            "--yes",
            "--json",
        ],
        output=output,
    )
    assert code == 4
    assert "sdk_migration_bundle_runtime_running" in output.getvalue()


def test_migration_bundle_previews_before_writing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()

    code = cli.run(
        [
            "migration-bundle",
            "--bundle-id",
            "migration-1",
            "--runtime-build",
            "build-13",
            "--source-sdk-version",
            "2.0.4",
            "--json",
        ],
        output=output,
    )
    assert code == 4
    assert '"state": "preview"' in output.getvalue() or '"state":"preview"' in output.getvalue()


def test_migration_bundle_success_reports_bundle_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )

    class Result:
        bundle_id = "migration-1"
        bundle_path = tmp_path / "backups" / "migration-1.sdk-bundle"
        file_count = 4
        total_bytes = 123

    monkeypatch.setattr(
        "one_memory.sdk_migration_bundle.create_sdk_migration_bundle",
        lambda **_kwargs: Result(),
    )
    output = io.StringIO()
    code = cli.run(
        [
            "migration-bundle",
            "--bundle-id",
            "migration-1",
            "--runtime-build",
            "build-13",
            "--source-sdk-version",
            "2.0.4",
            "--yes",
            "--json",
        ],
        output=output,
    )
    assert code == 0
    document = output.getvalue().replace(" ", "")
    assert '"state":"created"' in document
    assert '"bundle_id":"migration-1"' in document
