import io
import sqlite3
from types import SimpleNamespace

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
            "--yes",
            "--json",
        ],
        output=output,
    )
    assert code == 0
    document = output.getvalue().replace(" ", "")
    assert '"state":"created"' in document
    assert '"bundle_id":"migration-1"' in document


def test_migration_bundle_reuses_an_existing_verified_bundle(tmp_path, monkeypatch) -> None:
    from one_memory.sdk_migration_bundle import MigrationBundleRejected

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )

    def exists(**_kwargs):
        raise MigrationBundleRejected("sdk_migration_bundle_exists")

    monkeypatch.setattr(
        "one_memory.sdk_migration_bundle.create_sdk_migration_bundle",
        exists,
    )
    monkeypatch.setattr(
        "one_memory.sdk_migration_bundle.inspect_sdk_migration_bundle",
        lambda **_kwargs: {"file_count": 4, "total_bytes": 123},
    )
    output = io.StringIO()

    code = cli.run(
        [
            "migration-bundle",
            "--bundle-id",
            "migration-1",
            "--runtime-build",
            "build-13",
            "--yes",
            "--json",
        ],
        output=output,
    )

    assert code == 0
    assert '"state": "reused"' in output.getvalue()


def test_sdk_migrate_preview_never_commits_cutover(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()
    code = cli.run(
        [
            "sdk-migrate",
            "--migration-id",
            "migration-1",
            "--bundle-id",
            "bundle-1",
            "--json",
        ],
        output=output,
    )
    assert code == 4
    document = output.getvalue().replace(" ", "")
    assert '"state":"preview"' in document
    assert '"commits_cutover":false' in document


def test_sdk_migrate_runs_offline_backfill_without_cutover(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    monkeypatch.setattr(
        "one_memory.sdk_migration_bundle.inspect_sdk_migration_bundle",
        lambda **_kwargs: {"runtime_build": "v1.0.4"},
    )

    async def fake_migrate(*_args, **_kwargs):
        return None

    monkeypatch.setattr("one_memory.memory_server.migration_runner.migrate", fake_migrate)

    profile = SimpleNamespace(
        has_api_key=False,
        embedding_provider_kind="openai",
        embedding_model="embedding",
        embedding_dimensions=1024,
    )

    class Profiles:
        def __init__(self, _database):
            pass

        def active_identity(self):
            return ("profile", 1)

        def snapshot(self, *_identity):
            return profile

        def resolve_snapshot_secret(self, *_args):
            return None

    class Secrets:
        pass

    class Lease:
        client = object()

        def close(self):
            return None

    async def backfill(**_kwargs):
        return SimpleNamespace(
            state="ready",
            phase="reconciled",
            migration_id="migration-1",
            counts={"verified": 2},
            memory_items=1,
            raw_items=1,
        )

    monkeypatch.setattr("one_memory.model_profiles.ModelProfileStore", Profiles)
    monkeypatch.setattr("one_memory.secrets.KeyringSecretStore", Secrets)
    monkeypatch.setattr(
        "one_memory.hy_sdk_runtime.acquire_client",
        lambda *_args, **_kwargs: Lease(),
    )
    monkeypatch.setattr(
        "one_memory.sdk_migration_runner.run_sdk_data_plane_backfill",
        backfill,
    )
    output = io.StringIO()
    code = cli.run(
        [
            "sdk-migrate",
            "--migration-id",
            "migration-1",
            "--bundle-id",
            "bundle-1",
            "--yes",
            "--json",
        ],
        output=output,
    )
    assert code == 0
    document = output.getvalue().replace(" ", "")
    assert '"state":"ready"' in document
    assert '"commits_cutover":false' in document


def test_sdk_cutover_is_offline_confirmed_and_returns_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    monkeypatch.setattr(
        "one_memory.sdk_migration_runner.commit_reconciled_cutover",
        lambda **_kwargs: SimpleNamespace(
            migration_id="migration-1",
            backup_bundle_id="bundle-1",
            sdk_version="2.0.5.dev3",
            completed_at="2026-10-21T00:00:00Z",
        ),
    )
    output = io.StringIO()

    code = cli.run(
        [
            "sdk-cutover",
            "--migration-id",
            "migration-1",
            "--bundle-id",
            "bundle-1",
            "--yes",
            "--json",
        ],
        output=output,
    )

    assert code == 0
    document = output.getvalue().replace(" ", "")
    assert '"state":"cutover"' in document
    assert '"sdk_version":"2.0.5.dev3"' in document


def test_sdk_cutover_never_runs_while_runtime_is_live(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("running", "healthy"),
    )
    output = io.StringIO()

    code = cli.run(
        [
            "sdk-cutover",
            "--migration-id",
            "migration-1",
            "--bundle-id",
            "bundle-1",
            "--yes",
            "--json",
        ],
        output=output,
    )

    assert code == 4
    assert '"code": "sdk_cutover_runtime_running"' in output.getvalue()


def test_sdk_migration_status_distinguishes_empty_and_legacy_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    empty = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=empty) == 0
    assert '"state": "empty"' in empty.getvalue()

    database = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO evidence VALUES ('e1')")
    required = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=required) == 0
    assert '"state": "required"' in required.getvalue()
    assert '"raw": 1' in required.getvalue()
