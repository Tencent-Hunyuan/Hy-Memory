import os
import signal
import sys

# A handed-off capture ends itself: the hook that gave it the prompt has
# exited, and a host that gives up kills only the hook's own shell. The
# prompt hook passes a tighter deadline; every other capture hook passes none.
CAPTURE_DEADLINE_S = 120


def _end_by(seconds: str | None) -> None:
    """The kernel ends the process at the deadline, whatever it is stuck in.

    An unfinished write rolls back. Only a process entry sets it, never `run`,
    which tests call in-process.
    """

    if not hasattr(signal, "alarm"):
        return
    given = seconds or ""
    limit = int(given) if given.isascii() and given.isdigit() else 0
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(limit if 0 < limit <= CAPTURE_DEADLINE_S else CAPTURE_DEADLINE_S)


# As the entry (`python -m`, the frozen build) it is armed before the imports
# below; `main` arms it for the installed console script.
if __name__ == "__main__" and sys.argv[1:2] == ["capture"]:
    _end_by(os.environ.get("ONE_MEMORY_CAPTURE_DEADLINE_S"))

import contextlib  # noqa: E402
import errno  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator, Sequence  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TextIO  # noqa: E402

from one_memory.config import load_settings  # noqa: E402
from one_memory.runtime_client import RuntimeStatus, inspect_runtime  # noqa: E402

# The startup dialog can only act on the name it is given: running out of disk
# and a damaged backup are different next steps. These are the names that reach
# it.
_SAFE_MIGRATION_CODE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_OUT_OF_SPACE = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def migration_failure_code(
    error: BaseException, *, fallback: str = "sdk_migration_preflight_failed"
) -> str:
    """Name a migration failure; never guess a code from free text.

    Errs toward the caller's generic code: an exception this does not recognize
    keeps its message out of the document Desktop reads.
    """
    chain = list(_exception_chain(error))
    for item in chain:
        if isinstance(item, OSError) and item.errno in _OUT_OF_SPACE:
            return "sdk_migration_disk_space_insufficient"
    for item in chain:
        code = getattr(item, "code", "")
        # `sdk_data_plane_write_job_input_missing:<job_id>`: the name is the
        # code; the id is for the log, not for the dialog.
        code = code.split(":", 1)[0] if isinstance(code, str) else code
        if not isinstance(code, str) or not _SAFE_MIGRATION_CODE.match(code):
            continue
        if code.startswith(("sdk_data_plane_", "sdk_migration_")):
            return code
    return fallback


def main() -> None:
    if sys.argv[1:] == ["--keychain-read"]:
        from one_memory.keychain_reader import main as read_keychain

        raise SystemExit(read_keychain())
    if sys.argv[1:] == ["--keychain-write"]:
        from one_memory.keychain_writer import main as write_keychain

        raise SystemExit(write_keychain())
    if sys.argv[1:] == ["--keychain-delete"]:
        from one_memory.keychain_deleter import main as delete_keychain

        raise SystemExit(delete_keychain())
    if sys.argv[1:] == ["--keychain-delete", "--many"]:
        from one_memory.keychain_deleter import main as delete_keychain

        raise SystemExit(delete_keychain())
    if sys.argv[1:2] == ["capture"] and __name__ != "__main__":
        _end_by(os.environ.get("ONE_MEMORY_CAPTURE_DEADLINE_S"))

    raise SystemExit(run(sys.argv[1:]))


def run(arguments: Sequence[str], *, output: TextIO | None = None) -> int:
    stream = output or sys.stdout
    if not arguments or list(arguments) in (
        ["serve"],
        ["serve", "--maintenance"],
    ):
        serve(maintenance=list(arguments) == ["serve", "--maintenance"])
        return 0
    command, *options = arguments
    if command == "restore":
        return restore(options, stream)
    if command == "sdk-migrate":
        return sdk_migrate(options, stream)
    if command == "sdk-migration-status":
        return sdk_migration_status(options, stream)
    if command == "capture" and len(options) == 1:
        from one_memory.live_capture import capture_stdio

        return capture_stdio(options[0], sys.stdin, stream)
    if command not in {"status", "doctor"} or options not in ([], ["--json"]):
        stream.write(
            "Usage: one-memory [serve [--maintenance]|status [--json]|doctor [--json]"
            "|restore --source <managed-backup> --yes [--json]"
            "|sdk-migrate --runtime-build <build> --yes [--json]"
            "|sdk-migration-status [--json]"
            "|capture SOURCE]\n"
        )
        return 2

    status = inspect_runtime(load_settings().data_dir)
    if "--json" in options:
        stream.write(f"{json.dumps(status.json_value(), ensure_ascii=False)}\n")
    else:
        stream.write(f"{_status_text(command, status)}\n")
    return 0 if status.state == "running" else 3 if status.state == "stopped" else 1


def restore(options: Sequence[str], stream: TextIO) -> int:
    """Restore one managed backup while the Runtime is stopped.

    This is the maintenance entry the CLI calls after stopping the Runtime;
    it deliberately does not talk to a running Core, because a live Runtime
    owns the database it would replace.
    """
    source: str | None = None
    confirmed = False
    as_json = False
    index = 0
    while index < len(options):
        option = options[index]
        if option == "--source" and index + 1 < len(options):
            source = options[index + 1]
            index += 2
            continue
        if option == "--yes":
            confirmed = True
            index += 1
            continue
        if option == "--json":
            as_json = True
            index += 1
            continue
        stream.write(f"one-memory: 参数无效：{option}\n")
        return 2
    if source is None:
        stream.write("one-memory: restore 需要 --source <managed-backup>\n")
        return 2

    settings = load_settings()
    status = inspect_runtime(settings.data_dir)
    if status.state != "stopped":
        document = {
            "schema_version": 1,
            "state": "blocked",
            "code": "backup_restore_runtime_running",
            "runtime_state": status.state,
        }
        _write_restore(stream, as_json, document, "Runtime 仍在运行；先停止 Runtime 再恢复。")
        return 4
    if not confirmed:
        document = {
            "schema_version": 1,
            "state": "preview",
            "source": source,
            "requires_confirmation": True,
        }
        _write_restore(stream, as_json, document, "预览通过；加 --yes 才会覆盖当前数据库。")
        return 4
    from one_memory.managed_restore import RestoreRejected, restore_managed_backup
    from one_memory.sdk_migration_bundle import (
        MigrationBundleRejected,
        restore_sdk_migration_bundle,
    )

    is_sdk_bundle = source.endswith(".sdk-bundle")
    if is_sdk_bundle:
        try:
            sdk_result = restore_sdk_migration_bundle(
                data_dir=settings.data_dir,
                source=source,
                runtime_stopped=True,
            )
        except MigrationBundleRejected as error:
            document = {
                "schema_version": 1,
                "state": "failed",
                "code": error.code,
            }
            _write_restore(stream, as_json, document, f"恢复失败：{error.code}")
            return 1
        document = {
            "schema_version": 1,
            "state": "restored",
            "bundle_id": sdk_result.bundle_id,
            "restored_files": sdk_result.restored_files,
            "restored_bytes": sdk_result.restored_bytes,
            "previous_database_path": str(sdk_result.previous_database_path),
            "previous_sdk_root": (
                str(sdk_result.previous_sdk_root) if sdk_result.previous_sdk_root else None
            ),
        }
        _write_restore(
            stream,
            as_json,
            document,
            f"已恢复完整迁移备份：{sdk_result.bundle_id}。",
        )
        return 0
    try:
        managed_result = restore_managed_backup(data_dir=settings.data_dir, source=source)
    except RestoreRejected as error:
        document = {
            "schema_version": 1,
            "state": "failed",
            "code": error.code,
        }
        _write_restore(stream, as_json, document, f"恢复失败：{error.code}")
        return 1
    document = {
        "schema_version": 1,
        "state": "restored",
        "restored_bytes": managed_result.restored_bytes,
        "previous_backup_path": str(managed_result.previous_backup_path),
        "source_schema_version": managed_result.source_schema_version,
    }
    _write_restore(
        stream,
        as_json,
        document,
        f"已恢复：{managed_result.previous_backup_path} 为保护性备份。",
    )
    return 0


def _write_restore(
    stream: TextIO,
    as_json: bool,
    document: dict[str, object],
    text: str,
) -> None:
    if as_json:
        stream.write(f"{json.dumps(document, ensure_ascii=False)}\n")
    else:
        stream.write(f"{text}\n")


def sdk_migrate(options: Sequence[str], stream: TextIO) -> int:
    """Bring a stopped home to SDK authority in one resumable command.

    Backs up the home, makes queue work independent of the legacy tables,
    commits SDK authority and drops those tables. The SDK already holds every
    memory and raw turn, so nothing is copied. With `--json`, each step is one
    progress line and the last line is the result.
    """
    runtime_build: str | None = None
    confirmed = False
    as_json = False
    index = 0
    while index < len(options):
        option = options[index]
        if option == "--runtime-build" and index + 1 < len(options):
            runtime_build = options[index + 1]
            index += 2
            continue
        if option == "--yes":
            confirmed = True
            index += 1
            continue
        if option == "--json":
            as_json = True
            index += 1
            continue
        stream.write(f"one-memory: 参数无效：{option}\n")
        return 2
    if not runtime_build:
        stream.write("one-memory: sdk-migrate 需要 --runtime-build <build>\n")
        return 2

    from one_memory.sdk_migration_log import authority_snapshot, write_migration_log

    settings = load_settings()
    database_path = settings.data_dir / "one-memory.sqlite3"
    started = time.monotonic()
    write_migration_log(
        settings.data_dir,
        writer="core-sdk-migrate",
        event="migrate.invoked",
        outcome="started",
        details={
            "runtime_build": runtime_build,
            "confirmed": confirmed,
            # The Runtime the upgrade replaced, as the orchestrator read it
            # from that Runtime's install manifest; never guessed.
            "source_version": _migration_source("ONE_MEMORY_MIGRATION_SOURCE_VERSION"),
            "source_build": _migration_source("ONE_MEMORY_MIGRATION_SOURCE_BUILD"),
            **authority_snapshot(database_path),
        },
    )
    status = inspect_runtime(settings.data_dir)
    if status.state != "stopped":
        document = {
            "schema_version": 1,
            "state": "blocked",
            "code": "sdk_migration_runtime_running",
            "runtime_state": status.state,
        }
        write_migration_log(
            settings.data_dir,
            writer="core-sdk-migrate",
            event="migrate.blocked",
            outcome="blocked",
            code="sdk_migration_runtime_running",
            details={"runtime_state": status.state},
        )
        _write_restore(stream, as_json, document, "Runtime 仍在运行；不能迁移。")
        return 4
    if not confirmed:
        document = {"schema_version": 1, "state": "preview", "requires_confirmation": True}
        _write_restore(stream, as_json, document, "预览通过；加 --yes 后执行迁移。")
        return 4

    from one_memory.hy_runtime_config import (
        HY_MEMORY_NAMESPACE,
        HY_MEMORY_VERSION,
        configure_hy_runtime_environment,
    )

    configure_hy_runtime_environment(settings.data_dir)
    from one_memory.sdk_migration_runner import run_startup_migration

    step_names = {
        "bundle": "正在备份记忆数据……",
        "prepare": "正在整理待处理的写入任务……",
        "cutover": "正在切换数据源……",
        "vectors": "正在接回旧版本的向量数据……",
        "verify": "正在用新版本核对记忆能否读回……",
        "cleanup": "正在删除旧表……",
    }

    # Everything before the first progress step (authority read, profile
    # read, resumable identity, legacy id scan, model-profile check) is the
    # preflight. A failure is logged with the phase it happened in.
    phase = {"name": "preflight", "started": time.monotonic()}

    def progress(step: str) -> None:
        now = time.monotonic()
        write_migration_log(
            settings.data_dir,
            writer="core-sdk-migrate",
            event="phase.finished",
            outcome="succeeded",
            details={"phase": phase["name"], "duration_ms": (now - phase["started"]) * 1000},
        )
        phase["name"], phase["started"] = step, now
        write_migration_log(
            settings.data_dir,
            writer="core-sdk-migrate",
            event="phase.started",
            outcome="started",
            details={"phase": step},
        )
        if as_json:
            stream.write(json.dumps({"schema_version": 1, "state": "running", "step": step}) + "\n")
        else:
            stream.write(f"{step_names.get(step, step)}\n")
        stream.flush()

    restore: dict[str, object] = {}
    try:
        with _migration_lock(settings.data_dir):
            result = run_startup_migration(
                data_dir=settings.data_dir,
                runtime_build=runtime_build,
                sdk_version=HY_MEMORY_VERSION,
                namespace=HY_MEMORY_NAMESPACE,
                progress=progress,
                restore_report=restore,
            )
    except Exception as error:
        from one_memory.sdk_migration_verify import MemoryReadbackFailed

        code = migration_failure_code(error)
        failed: dict[str, object] = {"schema_version": 1, "state": "failed", "code": code}
        if isinstance(error, MemoryReadbackFailed):
            failed["readback"] = error.report.as_document()
        # The migration's own code stays the result; the restore is secondary.
        failed["data_restored"] = restore.get("state") == "restored"
        if restore.get("state") == "failed":
            failed["restore_code"] = restore.get("code")
        write_migration_log(
            settings.data_dir,
            writer="core-sdk-migrate",
            event="migrate.failed",
            outcome="failed",
            code=code,
            error=error,
            details={
                "phase": phase["name"],
                "phase_duration_ms": (time.monotonic() - phase["started"]) * 1000,
                "duration_ms": (time.monotonic() - started) * 1000,
                # A generic fallback code means the cause is only in `error`.
                "code_is_fallback": code == "sdk_migration_preflight_failed"
                and not _names_code(error, code),
                **({"readback": failed["readback"]} if "readback" in failed else {}),
                "restore": restore.get("state", "not_attempted"),
                **({"restore_code": restore["code"]} if "code" in restore else {}),
                **authority_snapshot(database_path),
            },
        )
        restored_text = "；数据已恢复到升级前的状态" if failed["data_restored"] else ""
        _write_restore(stream, as_json, failed, f"迁移未完成：{code}{restored_text}")
        return 1
    document = result.as_document()
    write_migration_log(
        settings.data_dir,
        writer="core-sdk-migrate",
        event="migrate.finished",
        outcome="succeeded",
        migration_id=result.migration_id,
        details={
            "state": result.state,
            "last_phase": phase["name"],
            "duration_ms": (time.monotonic() - started) * 1000,
            "result": document,
            **authority_snapshot(database_path),
        },
    )
    messages = {
        "not_required": "不需要迁移。",
        "complete": "迁移已完成；遗留的旧表或向量已处理。",
        "cutover": "迁移完成：记忆数据已以 SDK 为准。",
    }
    _write_restore(stream, as_json, document, messages[result.state])
    return 0


_SAFE_SOURCE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+\-]{0,63}$")


def _migration_source(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if _SAFE_SOURCE.fullmatch(value) else "unknown"


@contextlib.contextmanager
def _migration_lock(data_dir: Path) -> Iterator[None]:
    """One `sdk-migrate` per home at a time.

    Desktop, CLI install/upgrade and the Runtime service each migrate before
    serving, and a login-started service can be migrating when the person
    opens the app. The second waits here and then finds the work done (or
    resumes it); two runs never interleave.
    """
    from one_memory.sdk_migration_log import write_migration_log

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(data_dir / "sdk-migration.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            def acquire(blocking: bool) -> None:
                while True:
                    try:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        return
                    except OSError:
                        if not blocking:
                            raise
                        time.sleep(0.5)

            def release() -> None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire(blocking: bool) -> None:
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))

            def release() -> None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)

        try:
            acquire(False)
        except OSError:
            write_migration_log(
                data_dir,
                writer="core-sdk-migrate",
                event="migrate.waiting_for_lock",
                outcome="started",
            )
            waited = time.monotonic()
            acquire(True)
            write_migration_log(
                data_dir,
                writer="core-sdk-migrate",
                event="migrate.lock_acquired",
                outcome="succeeded",
                details={"waited_ms": (time.monotonic() - waited) * 1000},
            )
        try:
            yield
        finally:
            release()
    finally:
        os.close(descriptor)


def _names_code(error: BaseException, code: str) -> bool:
    return any(
        str(getattr(item, "code", "")).split(":", 1)[0] == code for item in _exception_chain(error)
    )


def sdk_migration_status(options: Sequence[str], stream: TextIO) -> int:
    """Read-only probe: does this home need `sdk-migrate`?"""
    try:
        return _sdk_migration_status(options, stream)
    except Exception as error:
        from one_memory.sdk_migration_log import write_migration_log

        # The orchestrator only sees a non-zero exit and reports
        # `sdk_migration_status_unavailable`; the cause is recorded here.
        write_migration_log(
            load_settings().data_dir,
            writer="core-sdk-migration-status",
            event="status.failed",
            outcome="failed",
            code="sdk_migration_status_unavailable",
            error=error,
        )
        raise


def _sdk_migration_status(options: Sequence[str], stream: TextIO) -> int:
    if list(options) not in ([], ["--json"]):
        stream.write("one-memory: sdk-migration-status 只接受 --json\n")
        return 2
    as_json = "--json" in options
    settings = load_settings()
    database_path = settings.data_dir / "one-memory.sqlite3"
    from one_memory.sdk_migration_restore import restore_pending

    if restore_pending(settings.data_dir):
        # A restore was killed between its renames; the database may be
        # elsewhere. Only `sdk-migrate` may finish it.
        document: dict[str, object] = {
            "schema_version": 1,
            "authority": "legacy",
            "state": "required",
        }
    elif not database_path.is_file():
        document = {
            "schema_version": 1,
            "authority": "sdk",
            "state": "not_required",
        }
    else:
        from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE
        from one_memory.sdk_data_plane_migration import (
            MemoryAuthority,
            SdkDataPlaneMigrationStore,
        )
        from one_memory.sdk_migration_runner import abandoned_staging_present
        from one_memory.sdk_migration_verify import read_active_profile
        from one_memory.sdk_vector_reattach import legacy_vector_collections
        from one_memory.target_schema import legacy_tables_present

        authority = SdkDataPlaneMigrationStore(database_path).authority()
        if authority is MemoryAuthority.SDK:
            profile = read_active_profile(database_path)
            leftovers = bool(
                legacy_tables_present(database_path)
                or abandoned_staging_present(
                    data_dir=settings.data_dir,
                    namespace=HY_MEMORY_NAMESPACE,
                )
                or (profile is not None and legacy_vector_collections(settings.data_dir, profile))
            )
            state = "required" if leftovers else "not_required"
        else:
            state = "required"
        document = {"schema_version": 1, "authority": authority.value, "state": state}
    from one_memory.sdk_migration_log import write_migration_log

    write_migration_log(
        settings.data_dir,
        writer="core-sdk-migration-status",
        event="status.probed",
        outcome="succeeded",
        details={"authority": document["authority"], "state": document["state"]},
    )
    if as_json:
        stream.write(f"{json.dumps(document, ensure_ascii=False)}\n")
    else:
        stream.write(f"SDK migration: {document['state']} (authority={document['authority']})\n")
    return 0


def _status_text(command: str, status: RuntimeStatus) -> str:
    if status.state == "running":
        location = f"{status.host}:{status.port}"
        version = f"，版本 {status.version}" if status.version else ""
        return f"MemHub Runtime 正在运行（{location}{version}）。"
    if status.state == "stopped":
        return "MemHub Runtime 未运行。"
    if command == "doctor":
        return f"MemHub Runtime 检查失败：{status.code}。"
    return f"MemHub Runtime 状态不可验证：{status.code}。"


#: Exit status of a serving Core refused because the home is not on SDK
#: authority (`EX_CONFIG`). The Runtime maps it to `sdk_migration_required`.
SDK_MIGRATION_REQUIRED_EXIT = 78


def _refuse_unmigrated_home(data_dir: Path) -> None:
    """Exit before binding when an existing home has not reached SDK authority.

    A brand-new home has no database yet and is created on SDK authority by
    startup. Anything else that is not SDK is an upgrade that did not finish,
    and is never served: the migration must run first.
    """
    from one_memory.sdk_data_plane_migration import MemoryAuthority, SdkDataPlaneMigrationStore
    from one_memory.sdk_migration_log import authority_snapshot, write_migration_log
    from one_memory.sdk_migration_restore import restore_pending

    database_path = data_dir / "one-memory.sqlite3"
    if restore_pending(data_dir):
        # A killed restore may have moved the database aside: serving now
        # would create an empty home over the user's data.
        write_migration_log(
            data_dir,
            writer="core-startup",
            event="core.refused_legacy",
            outcome="refused",
            code="sdk_migration_required",
            details={"stage": "serve_entry", "restore_pending": True},
        )
        print("sdk_migration_required", file=sys.stderr, flush=True)
        raise SystemExit(SDK_MIGRATION_REQUIRED_EXIT)
    if not database_path.is_file():
        return
    try:
        authority = SdkDataPlaneMigrationStore(database_path).authority()
    except Exception as error:  # noqa: BLE001 - an unreadable home is not served either
        write_migration_log(
            data_dir,
            writer="core-startup",
            event="core.refused_legacy",
            outcome="refused",
            code="sdk_migration_required",
            error=error,
        )
        print("sdk_migration_required", file=sys.stderr, flush=True)
        raise SystemExit(SDK_MIGRATION_REQUIRED_EXIT) from None
    if authority is MemoryAuthority.SDK:
        return
    write_migration_log(
        data_dir,
        writer="core-startup",
        event="core.refused_legacy",
        outcome="refused",
        code="sdk_migration_required",
        details={"stage": "serve_entry", **authority_snapshot(database_path)},
    )
    print("sdk_migration_required", file=sys.stderr, flush=True)
    raise SystemExit(SDK_MIGRATION_REQUIRED_EXIT)


def serve(*, maintenance: bool = False) -> None:
    import uvicorn

    from one_memory.diagnostic_log import configure_diagnostics, diagnostic_event
    from one_memory.hy_runtime_config import (
        apply_diagnostic_log_level,
        configure_hy_runtime_environment,
        diagnostic_log_level,
    )
    from one_memory.log_file import start_logging_to_file
    from one_memory.parent_watchdog import ParentWatchdog
    from one_memory.runtime import CoreRuntime, parent_pid

    settings = load_settings()
    if not maintenance:
        _refuse_unmigrated_home(settings.data_dir)
    # HY resolves some trace paths at import time and its pipeline path when
    # SharedRuntime starts. Bind both to the MemHub root before importing the
    # application graph; otherwise HY falls back to ~/.hy_memory while the
    # desktop Debug panel watches the managed data directory.
    configure_hy_runtime_environment(settings.data_dir)
    from one_memory.api import create_app

    start_logging_to_file(settings.data_dir)
    apply_diagnostic_log_level(diagnostic_log_level(settings.data_dir))
    configure_diagnostics(
        settings.data_dir,
        session_id=os.environ.get("ONE_MEMORY_CORE_SESSION_ID"),
    )
    diagnostic_event(
        level="info",
        component="core.lifecycle",
        event="core.process_started",
        outcome="started",
        instance_id=os.environ.get("ONE_MEMORY_INSTANCE_ID"),
        details={"parent_session_id": os.environ.get("ONE_MEMORY_PARENT_SESSION_ID") or "none"},
    )
    # Started before the server so that a parent which dies during a slow
    # startup is still noticed. Absent when Core is run on its own, where
    # nothing should be tying its lifetime to another process.
    launcher = parent_pid()
    if launcher is not None:
        ParentWatchdog(launcher).start()
    application = create_app(
        settings,
        manage_runtime=not maintenance,
    )
    if maintenance:
        with CoreRuntime(settings, publish_receipt=False):
            uvicorn.run(
                application,
                host=settings.host,
                port=settings.port,
                reload=False,
                access_log=False,
            )
    else:
        uvicorn.run(
            application,
            host=settings.host,
            port=settings.port,
            reload=False,
            access_log=False,
        )


if __name__ == "__main__":
    main()
