import os
import signal
import socket
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

import errno  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from collections.abc import Iterator, Sequence  # noqa: E402
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
        ["serve", "--reader"],
    ):
        serve(
            maintenance=list(arguments) == ["serve", "--maintenance"],
            reader=list(arguments) == ["serve", "--reader"],
        )
        return 0
    command, *options = arguments
    if command == "restore":
        return restore(options, stream)
    if command == "sdk-migrate":
        return sdk_migrate(options, stream)
    if command == "sdk-migration-status":
        return sdk_migration_status(options, stream)
    if command == "boundary" and not options:
        from one_memory.workbuddy_boundary import write_hook

        return write_hook(stream, sys.stdin)
    if command == "boundary" and options == ["--peek"]:
        from one_memory.workbuddy_boundary import write_peek

        return write_peek(stream, sys.stdin)
    if command == "boundary" and len(options) == 2 and options[0] == "--ack":
        from one_memory.workbuddy_boundary import run_confirm

        return run_confirm(options[1])
    if command == "capture" and len(options) == 1:
        from one_memory.live_capture import capture_stdio

        return capture_stdio(options[0], sys.stdin, stream)
    if command not in {"status", "doctor"} or options not in ([], ["--json"]):
        stream.write(
            "Usage: one-memory [serve [--maintenance|--reader]|status [--json]|doctor [--json]"
            "|restore --source <managed-backup> --yes [--json]"
            "|sdk-migrate --runtime-build <build> --yes [--json]"
            "|sdk-migration-status [--json]"
            "|boundary [--peek|--ack TOKEN]|capture SOURCE]\n"
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

    settings = load_settings()
    status = inspect_runtime(settings.data_dir)
    if status.state != "stopped":
        document = {
            "schema_version": 1,
            "state": "blocked",
            "code": "sdk_migration_runtime_running",
            "runtime_state": status.state,
        }
        _write_restore(stream, as_json, document, "Runtime 仍在运行；不能迁移。")
        return 4
    if not confirmed:
        document = {"schema_version": 1, "state": "preview", "requires_confirmation": True}
        _write_restore(stream, as_json, document, "预览通过；加 --yes 后执行迁移。")
        return 4

    from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE, HY_MEMORY_VERSION
    from one_memory.sdk_migration_runner import run_startup_migration

    step_names = {
        "bundle": "正在备份记忆数据……",
        "prepare": "正在整理待处理的写入任务……",
        "cutover": "正在切换数据源……",
        "vectors": "正在接回旧版本的向量数据……",
        "verify": "正在用新版本核对记忆能否读回……",
        "cleanup": "正在删除旧表……",
    }

    def progress(step: str) -> None:
        if as_json:
            stream.write(
                json.dumps({"schema_version": 1, "state": "running", "step": step}) + "\n"
            )
        else:
            stream.write(f"{step_names.get(step, step)}\n")
        stream.flush()

    try:
        result = run_startup_migration(
            data_dir=settings.data_dir,
            runtime_build=runtime_build,
            sdk_version=HY_MEMORY_VERSION,
            namespace=HY_MEMORY_NAMESPACE,
            progress=progress,
        )
    except Exception as error:
        from one_memory.sdk_migration_verify import MemoryReadbackFailed

        code = migration_failure_code(error)
        failed: dict[str, object] = {"schema_version": 1, "state": "failed", "code": code}
        if isinstance(error, MemoryReadbackFailed):
            failed["readback"] = error.report.as_document()
        _write_restore(stream, as_json, failed, f"迁移未完成：{code}")
        return 1
    messages = {
        "not_required": "不需要迁移。",
        "complete": "迁移已完成；遗留的旧表或向量已处理。",
        "cutover": "迁移完成：记忆数据已以 SDK 为准。",
    }
    _write_restore(stream, as_json, result.as_document(), messages[result.state])
    return 0


def sdk_migration_status(options: Sequence[str], stream: TextIO) -> int:
    """Read-only probe: does this home need `sdk-migrate`?"""
    if list(options) not in ([], ["--json"]):
        stream.write("one-memory: sdk-migration-status 只接受 --json\n")
        return 2
    as_json = "--json" in options
    settings = load_settings()
    database_path = settings.data_dir / "one-memory.sqlite3"
    if not database_path.is_file():
        document: dict[str, object] = {
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
                or (
                    profile is not None
                    and legacy_vector_collections(settings.data_dir, profile)
                )
            )
            state = "required" if leftovers else "not_required"
        else:
            state = "required"
        document = {"schema_version": 1, "authority": authority.value, "state": state}
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


def _reserve_reader_port(host: str, port: int) -> int:
    """Resolve the port the Reader will listen on, including the ephemeral case.

    The supervisor asks for an ephemeral port (`ONE_MEMORY_READER_PORT=0`) and
    then reads the real one back from stderr. Letting uvicorn bind port 0
    itself would give a port nobody can report, so claim a free port here by
    binding and immediately releasing it, then serve on that exact port.
    """
    if port != 0:
        return port
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def serve(*, maintenance: bool = False, reader: bool = False) -> None:
    import uvicorn

    from one_memory.diagnostic_log import configure_diagnostics, diagnostic_event
    from one_memory.hy_runtime_config import (
        apply_diagnostic_log_level,
        diagnostic_log_level,
    )
    from one_memory.log_file import start_logging_to_file
    from one_memory.parent_watchdog import ParentWatchdog
    from one_memory.runtime import CoreRuntime, parent_pid

    settings = load_settings()
    if reader:
        settings = settings.model_copy(
            update={
                "port": int(os.environ.get("ONE_MEMORY_READER_PORT", "0") or "0"),
                "api_token": settings.api_token,
            }
        )
        from one_memory.reader_app import ReaderSettings, create_reader_app

        core_port = int(os.environ.get("ONE_MEMORY_CORE_PORT", "8765"))
        # The supervisor learns the Reader's port by watching stderr for
        # `reader_port:<n>`. With `ONE_MEMORY_READER_PORT=0` uvicorn would bind
        # an OS-chosen port that nothing reports back, so claim the port up
        # front and serve on that exact one.
        port = _reserve_reader_port(settings.host, settings.port)
        reader_settings = ReaderSettings(
            data_dir=settings.data_dir,
            host=settings.host,
            port=port,
            api_token=settings.api_token,
            core_port=core_port,
            core_api_token=os.environ.get("ONE_MEMORY_CORE_API_TOKEN", ""),
        )
        application = create_reader_app(reader_settings)
        print(f"reader_port:{port}", file=sys.stderr, flush=True)
        uvicorn.run(
            application,
            host=settings.host,
            port=port,
            reload=False,
            access_log=False,
        )
        return

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
