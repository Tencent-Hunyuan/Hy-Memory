"""Put a home back exactly as it was when a failed `sdk-migrate` started.

A migration that fails before SDK authority is committed has already changed
the home: schema migrations on `one-memory.sqlite3`, legacy work moved into the
durable queue, renamed Chroma collections under `hy-memory/governed-v2`, ledger
rows. The fixed release that follows must start from the original data, not
from that half-way state, so the runner restores the verified full-home bundle
it took before the first change (`restore point`).

The restore is staged and then swapped in with renames:

1. the bundle is verified (every file hashed against its manifest);
2. its database, config and SDK root are copied next to their live places and
   verified again;
3. a journal lists every rename, then the live items (database with its
   `-wal`/`-shm`, `config.json`, `hy-memory/governed-v2`) are renamed into
   `backups/sdk-migration-failed/` and the staged copies are renamed in.

A rename either happened or did not, and each step is idempotent, so a process
killed during step 3 is finished by the next run (`finish_interrupted_restore`)
before anything else reads the home. Core refuses to serve and the status
probe reports `required` while a journal exists.

After SDK authority is committed nothing is restored: the home already belongs
to the SDK and the legacy tables may be gone.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import close_main_databases
from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE
from one_memory.sdk_data_plane_migration import DataPlaneMigrationError

RESTORE_POINT = ".sdk-migration-restore-point.json"
RESTORE_STAGING = ".sdk-migration-restore"
JOURNAL = "journal.json"
FAILED_STATE = "sdk-migration-failed"
RESTORED_MARK = "restored.json"
DATABASE = "one-memory.sqlite3"
CONFIG = "config.json"
_DATABASE_SIDECARS = ("-wal", "-shm", "-journal")
_WRITER = "core-sdk-migrate"


class MigrationRestoreFailed(DataPlaneMigrationError):
    """The pre-migration data could not be put back; the home stays blocked."""


@dataclass(frozen=True)
class RestoreResult:
    bundle_id: str
    files: int
    bytes: int
    duration_ms: float

    def as_document(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "files": self.files,
            "bytes": self.bytes,
            "duration_ms": self.duration_ms,
        }


def _home(data_dir: Path) -> Path:
    return data_dir.resolve()


def _staging(home: Path) -> Path:
    return home / RESTORE_STAGING


def _journal(home: Path) -> Path:
    return _staging(home) / JOURNAL


def _sdk_staging(home: Path) -> Path:
    return home / "hy-memory" / f".{HY_MEMORY_NAMESPACE}.restore-staging"


def failed_state_path(data_dir: Path) -> Path:
    return _home(data_dir) / "backups" / FAILED_STATE


def _write_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    _fsync(temporary)
    temporary.replace(path)
    _fsync_directory(path.parent)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    # Windows cannot open a directory; its renames are durable on return.
    try:
        _fsync(path)
    except OSError:
        return


def _log(
    data_dir: Path,
    event: str,
    outcome: str,
    *,
    migration_id: str | None,
    code: str | None = None,
    error: BaseException | None = None,
    details: dict[str, object] | None = None,
) -> None:
    from one_memory.sdk_migration_log import write_migration_log

    write_migration_log(
        data_dir,
        writer=_WRITER,
        event=event,
        outcome=outcome,
        migration_id=migration_id,
        code=code,
        error=error,
        details=details,
    )


# --- restore point -----------------------------------------------------------


def record_restore_point(data_dir: Path, *, migration_id: str, bundle_id: str) -> None:
    """Name the verified bundle that holds this home as the migration found it.

    Written before the first change, so a run killed part-way is rolled back
    by the next one instead of resumed.
    """
    _write_json(
        _home(data_dir) / RESTORE_POINT,
        {
            "schema_version": 1,
            "migration_id": migration_id,
            "bundle_id": bundle_id,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


def read_restore_point(data_dir: Path) -> dict[str, str] | None:
    try:
        document = json.loads((_home(data_dir) / RESTORE_POINT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise MigrationRestoreFailed("sdk_migration_restore_point_invalid") from error
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(document.get("migration_id"), str)
        or not isinstance(document.get("bundle_id"), str)
    ):
        raise MigrationRestoreFailed("sdk_migration_restore_point_invalid")
    return {"migration_id": document["migration_id"], "bundle_id": document["bundle_id"]}


def clear_restore_point(data_dir: Path) -> None:
    (_home(data_dir) / RESTORE_POINT).unlink(missing_ok=True)


def restore_pending(data_dir: Path) -> bool:
    """A restore was interrupted between its renames; nothing may read the home."""
    return _journal(_home(data_dir)).is_file()


# --- restore -----------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_handles() -> None:
    """Close what this process holds on the home; Windows cannot rename open files."""
    close_main_databases()
    chromadb = sys.modules.get("chromadb")
    if chromadb is None:
        return
    try:
        from chromadb.api.shared_system_client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:  # noqa: BLE001 - best effort; a held file fails the rename instead
        return


def _verify_copy(copy: Path, expected: list[dict[str, object]], prefix: str) -> None:
    """The staged tree holds exactly the bundle's files under `prefix`."""
    wanted = {
        str(item["path"])[len(prefix) :]: item
        for item in expected
        if str(item["path"]).startswith(prefix)
    }
    present = (
        {path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()}
        if copy.is_dir()
        else set()
    )
    if present != set(wanted):
        raise MigrationRestoreFailed("sdk_migration_restore_verify_failed")
    for relative, item in wanted.items():
        path = copy / relative
        if path.stat().st_size != int(str(item["bytes"])) or _sha256(path) != item["sha256"]:
            raise MigrationRestoreFailed("sdk_migration_restore_verify_failed")


def _plan(home: Path, *, config_staged: bool) -> list[tuple[str, str]]:
    """Every rename of the swap, as paths relative to the home, in order."""
    failed = Path("backups") / FAILED_STATE
    staging = Path(RESTORE_STAGING)
    sdk_live = Path("hy-memory") / HY_MEMORY_NAMESPACE
    steps: list[tuple[Path, Path]] = [
        (Path(f"{DATABASE}{suffix}"), failed / "management" / f"{DATABASE}{suffix}")
        for suffix in _DATABASE_SIDECARS
    ]
    steps += [
        (Path(DATABASE), failed / "management" / DATABASE),
        (Path(CONFIG), failed / "management" / CONFIG),
        (sdk_live, failed / "sdk" / HY_MEMORY_NAMESPACE),
        (staging / "management" / DATABASE, Path(DATABASE)),
        (_sdk_staging(home).relative_to(home), sdk_live),
    ]
    if config_staged:
        steps.append((staging / "management" / CONFIG, Path(CONFIG)))
    return [(source.as_posix(), target.as_posix()) for source, target in steps]


def _apply(
    home: Path,
    steps: list[list[str]] | list[tuple[str, str]],
    done: list[tuple[Path, Path]],
) -> None:
    """Run each rename once; a step whose source is already gone is skipped.

    `done` collects the renames this call made, so a caller can undo them.
    """
    for source_name, target_name in steps:
        source, target = home / source_name, home / target_name
        source_exists = source.exists() or source.is_symlink()
        target_exists = target.exists() or target.is_symlink()
        if not source_exists:
            continue
        if target_exists:
            raise MigrationRestoreFailed("sdk_migration_restore_conflict")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source.replace(target)
        done.append((source, target))
    for directory in (home, home / "hy-memory"):
        _fsync_directory(directory)


def _finish(home: Path, bundle_id: str) -> None:
    # The restore point goes first: a kill after it leaves only the journal,
    # whose steps are all done, and the next run just tidies up.
    clear_restore_point(home)
    shutil.rmtree(_sdk_staging(home), ignore_errors=True)
    _journal(home).unlink(missing_ok=True)
    shutil.rmtree(_staging(home), ignore_errors=True)
    bundle = home / "backups" / f"{bundle_id}.sdk-bundle"
    try:
        # A later verified bundle supersedes this one (`prune_restored_bundles`).
        _write_json(
            bundle / RESTORED_MARK,
            {"schema_version": 1, "restored_at": datetime.now(UTC).isoformat()},
        )
    except OSError:
        return


def restore_pre_migration_state(
    data_dir: Path,
    *,
    bundle_id: str,
    migration_id: str | None,
    reason: str,
) -> RestoreResult:
    """Put the restore-point bundle back in place of the half-migrated home.

    The caller holds the migration lock with Runtime stopped and host capture
    fenced. Raises `MigrationRestoreFailed`; before the swap the home is left as
    the failure left it, and the restore point stays for the next run.
    """
    home = _home(data_dir)
    started = time.monotonic()
    _log(
        home,
        "restore.started",
        "started",
        migration_id=migration_id,
        details={"bundle_id": bundle_id, "reason": reason},
    )
    try:
        result = _restore(home, bundle_id=bundle_id, started=started)
    except BaseException as error:
        code = getattr(error, "code", None)
        if not (isinstance(code, str) and code.startswith("sdk_migration_restore")):
            code = (
                "sdk_migration_restore_disk_space_insufficient"
                if isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EDQUOT}
                else "sdk_migration_restore_failed"
            )
        _log(
            home,
            "restore.failed",
            "failed",
            migration_id=migration_id,
            code=code,
            error=error,
            details={
                "bundle_id": bundle_id,
                "reason": reason,
                "duration_ms": (time.monotonic() - started) * 1000,
                "swap_pending": restore_pending(home),
            },
        )
        if not isinstance(error, Exception) or (
            isinstance(error, MigrationRestoreFailed) and error.code == code
        ):
            raise
        raise MigrationRestoreFailed(code) from error
    _log(
        home,
        "restore.finished",
        "succeeded",
        migration_id=migration_id,
        details={**result.as_document(), "reason": reason},
    )
    return result


def _restore(home: Path, *, bundle_id: str, started: float) -> RestoreResult:
    from one_memory.sdk_migration_bundle import inspect_sdk_migration_bundle

    if restore_pending(home):
        raise MigrationRestoreFailed("sdk_migration_restore_pending")
    manifest = inspect_sdk_migration_bundle(data_dir=home, bundle_id=bundle_id)
    bundle = home / "backups" / f"{bundle_id}.sdk-bundle"
    listed = manifest.get("files")
    files = [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
    total = sum(int(str(item["bytes"])) for item in files)
    if shutil.disk_usage(home).free < total:
        raise MigrationRestoreFailed("sdk_migration_restore_disk_space_insufficient")
    _release_handles()

    staging = _staging(home)
    sdk_staging = _sdk_staging(home)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(sdk_staging, ignore_errors=True)
    (staging / "management").mkdir(parents=True, mode=0o700)
    try:
        shutil.copy2(bundle / "management" / DATABASE, staging / "management" / DATABASE)
        config_staged = manifest.get("config") is not None
        if config_staged:
            shutil.copy2(bundle / "management" / CONFIG, staging / "management" / CONFIG)
        _verify_copy(staging / "management", files, "management/")
        sdk_source = bundle / "sdk" / HY_MEMORY_NAMESPACE
        if not sdk_source.is_dir() or sdk_source.is_symlink():
            raise MigrationRestoreFailed("sdk_migration_restore_bundle_incomplete")
        sdk_staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(sdk_source, sdk_staging, symlinks=True)
        _verify_copy(sdk_staging, files, f"sdk/{HY_MEMORY_NAMESPACE}/")
        for path in (staging / "management").iterdir():
            _fsync(path)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(sdk_staging, ignore_errors=True)
        raise

    failed = failed_state_path(home)
    # One failed state is kept for diagnosis; the newest replaces the last.
    shutil.rmtree(failed, ignore_errors=True)
    if failed.exists():
        raise MigrationRestoreFailed("sdk_migration_restore_failed_state_busy")
    steps = _plan(home, config_staged=config_staged)
    _write_json(
        _journal(home),
        {"schema_version": 1, "bundle_id": bundle_id, "steps": [list(step) for step in steps]},
    )
    done: list[tuple[Path, Path]] = []
    try:
        _apply(home, steps, done)
    except BaseException as error:
        if not isinstance(error, Exception):
            # Interrupted (a signal tearing the process down): leave the
            # journal; the next run finishes the swap forward.
            raise
        try:
            for source, target in reversed(done):
                target.replace(source)
        except OSError:
            # The journal stays: the next run finishes the swap forward.
            raise MigrationRestoreFailed("sdk_migration_restore_swap_interrupted") from error
        _journal(home).unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(sdk_staging, ignore_errors=True)
        raise
    _finish(home, bundle_id)
    return RestoreResult(
        bundle_id=bundle_id,
        files=len(files),
        bytes=total,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def finish_interrupted_restore(data_dir: Path, *, migration_id: str | None = None) -> bool:
    """Complete a swap a killed process left between its renames. True if it did."""
    home = _home(data_dir)
    journal = _journal(home)
    if not journal.is_file():
        return False
    started = time.monotonic()
    try:
        document = json.loads(journal.read_text(encoding="utf-8"))
        steps = document["steps"]
        bundle_id = str(document["bundle_id"])
        if not isinstance(steps, list) or not all(
            isinstance(step, list) and len(step) == 2 for step in steps
        ):
            raise ValueError("journal steps invalid")
        _release_handles()
        _apply(home, steps, [])
        _finish(home, bundle_id)
    except Exception as error:
        code = (
            error.code
            if isinstance(error, MigrationRestoreFailed)
            else "sdk_migration_restore_resume_failed"
        )
        _log(
            home,
            "restore.failed",
            "failed",
            migration_id=migration_id,
            code=code,
            error=error,
            details={"reason": "interrupted_swap", "swap_pending": True},
        )
        if isinstance(error, MigrationRestoreFailed):
            raise
        raise MigrationRestoreFailed(code) from error
    _log(
        home,
        "restore.finished",
        "succeeded",
        migration_id=migration_id,
        details={
            "bundle_id": bundle_id,
            "reason": "interrupted_swap",
            "duration_ms": (time.monotonic() - started) * 1000,
        },
    )
    return True


def prune_restored_bundles(data_dir: Path, *, keep: set[str]) -> int:
    """Drop bundles already restored, once a newer verified bundle exists.

    Each holds the same pre-migration home a newer bundle now holds (capture
    stayed fenced since), so it is only a second copy of it.
    """
    backups = _home(data_dir) / "backups"
    removed = 0
    for mark in backups.glob(f"*.sdk-bundle/{RESTORED_MARK}"):
        bundle = mark.parent
        if bundle.name.removesuffix(".sdk-bundle") in keep:
            continue
        shutil.rmtree(bundle, ignore_errors=True)
        removed += 0 if bundle.exists() else 1
    return removed


def discard_failed_state(data_dir: Path) -> None:
    """The failed half-migrated copy is only useful until a migration succeeds."""
    shutil.rmtree(failed_state_path(data_dir), ignore_errors=True)
