"""The one log support asks for when an upgrade did not reach SDK authority.

`<data_dir>/logs/sdk-migration.log` is JSON Lines, shared with the TypeScript
orchestrator (`packages/runtime-control/src/sdk-migration-log.ts`), which writes
the same line shape. A person attaches this single file to a report, so every
line says who wrote it, which build, which migration and what happened.

It must never carry user content: no memory or conversation text, no provider
response, no exception message (a message can quote either). Errors are logged
as their type, a sanitised code and traceback frames as `file:line:function`.
Detail values are numbers, booleans and short identifier-shaped strings only;
anything else is dropped rather than escaped.

Writing is synchronous and flushed: a migration is rare and the line that
explains a failure is worth more than the milliseconds. A write failure is
swallowed, because logging must never be the reason a migration or a startup
fails.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_NAME = "sdk-migration.log"
#: Rotated to `sdk-migration.log.1` beyond this; one generation is kept.
MAX_BYTES = 4 * 1024 * 1024

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:/@+\-]{0,160}$")
_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,15}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9_.\-]{1,96}$")
_MAX_ITEMS = 32
_MAX_DEPTH = 4
_MAX_FRAMES = 24
_FRAME = re.compile(r"^[A-Za-z0-9_.\-]{1,96}:\d{1,7}:<?[A-Za-z0-9_]{1,96}>?$")


def log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / LOG_NAME


def _safe(value: object, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value if _SAFE_TEXT.fullmatch(value) else "<redacted>"
    if depth >= _MAX_DEPTH:
        return "<nested>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["<truncated>"] = len(value) - _MAX_ITEMS
                break
            if isinstance(key, str) and _SAFE_TEXT.fullmatch(key):
                result[key] = _safe(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        safe = [_safe(item, depth + 1) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            safe.append(f"<+{len(items) - _MAX_ITEMS}>")
        return safe
    return "<unsupported>"


def _chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def describe_error(error: BaseException) -> dict[str, object]:
    """Type, code and frames of an exception chain; never its message."""
    chain: list[dict[str, object]] = []
    for item in _chain(error):
        name = type(item).__name__
        entry: dict[str, object] = {"type": name if _SAFE_TEXT.fullmatch(name) else "<unknown>"}
        code = getattr(item, "code", None)
        if isinstance(code, str):
            # `<code>:<opaque id>` names the row that failed, e.g. a write job.
            name, _, subject = code.partition(":")
            if _CODE.fullmatch(name):
                entry["code"] = name
                if subject and _SUBJECT.fullmatch(subject):
                    entry["subject"] = subject
        message = str(item)
        # A message that is itself a stable code (the codebase's convention
        # for domain errors) is safe to keep; anything else is content.
        if _CODE.fullmatch(message):
            entry["code"] = entry.get("code") or message
        errno = getattr(item, "errno", None)
        if isinstance(errno, int):
            entry["errno"] = errno
        if isinstance(item, OSError) and item.filename is not None:
            # A path names a file, not its contents, but home directories can
            # name a person: keep only the file name.
            entry["file"] = Path(str(item.filename)).name
        frames = traceback.extract_tb(item.__traceback__)[-_MAX_FRAMES:]
        # Code locations, not content: built here from file names, line
        # numbers and function names (`<module>`, `<lambda>` included).
        entry["frames"] = [
            location
            for frame in frames
            if _FRAME.fullmatch(
                location := f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            )
        ]
        chain.append(entry)
        if len(chain) >= 6:
            break
    return {"chain": chain}


_PY_FRAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*\.py:\d{1,7}:[A-Za-z_][A-Za-z0-9_]*$")


def error_frames(error: BaseException, *, limit: int = 16) -> tuple[str, ...]:
    """The raised error's innermost `file.py:line:function` frames, for a stack field.

    Library frames are kept (a KeyError raised inside Chroma is located by
    them); `<module>`-style names are dropped because stack fields refuse them.
    """
    chain = describe_error(error)["chain"]
    frames = chain[0].get("frames", ()) if chain else ()  # type: ignore[index,union-attr]
    return tuple(frame for frame in frames if _PY_FRAME.fullmatch(frame))[-limit:]


def error_chain_text(error: BaseException) -> str:
    """`describe_error` as one log token: `Type@file:line:function>...;Cause@...`.

    For a plain text log (core.log). Types and frames only, never a message.
    """
    return ";".join(
        f"{entry['type']}@{'>'.join(entry.get('frames', ()))}"  # type: ignore[arg-type]
        for entry in describe_error(error)["chain"]  # type: ignore[union-attr]
    )


def _build() -> dict[str, object]:
    from one_memory import __version__

    build: dict[str, object] = {"core_version": __version__}
    for key, name in (
        ("ONE_MEMORY_VERSION", "app_version"),
        ("ONE_MEMORY_BUILD_NUMBER", "build_number"),
        ("ONE_MEMORY_COMMIT", "build_commit"),
        ("ONE_MEMORY_MIGRATION_CALLER", "caller"),
        ("ONE_MEMORY_MIGRATION_RUNTIME_BUILD", "runtime_build"),
    ):
        value = os.environ.get(key, "").strip()
        if value:
            build[name] = value
    return _safe(build)


def write_migration_log(
    data_dir: Path,
    *,
    writer: str,
    event: str,
    outcome: str,
    migration_id: str | None = None,
    details: Mapping[str, object] | None = None,
    error: BaseException | None = None,
    code: str | None = None,
) -> None:
    """Append one line. Never raises."""
    try:
        line: dict[str, object] = {
            "schema_version": 1,
            "ts": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "writer": writer,
            "event": event,
            "outcome": outcome,
            **_build(),
        }
        if migration_id is not None:
            line["migration_id"] = migration_id
        if code is not None:
            line["code"] = code
        if details:
            line["details"] = dict(details)
        safe_line = _safe(line)
        if not isinstance(safe_line, dict):
            return
        if error is not None:
            # Already content-free by construction; `_safe` would redact the
            # `<module>` frames.
            safe_line["error"] = describe_error(error)
        destination = log_path(data_dir)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            if destination.stat().st_size > MAX_BYTES:
                os.replace(destination, destination.with_name(f"{LOG_NAME}.1"))
        except FileNotFoundError:
            pass
        payload = (json.dumps(safe_line, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = os.open(destination, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:  # noqa: BLE001 - logging must never break a migration
        return


def migration_rows(database_path: Path) -> list[dict[str, object]]:
    """The recorded migration attempts, newest first; ids, states and phases only."""
    import sqlite3

    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT migration_id,state,phase,created_at,updated_at "
            "FROM sdk_data_plane_migrations ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [
        {
            "migration_id": row[0],
            "state": row[1],
            "phase": row[2],
            # Older builds stored `YYYY-MM-DD HH:MM:SS`; keep it identifier-shaped.
            "created_at": str(row[3]).replace(" ", "T"),
            "updated_at": str(row[4]).replace(" ", "T"),
        }
        for row in rows
    ]


def authority_snapshot(database_path: Path) -> dict[str, object]:
    """Authority and recorded attempts, read without changing the database."""
    if not database_path.is_file():
        return {"database": "absent"}
    snapshot: dict[str, object] = {"database": "present"}
    try:
        from one_memory.sdk_data_plane_migration import SdkDataPlaneMigrationStore

        authority, authority_migration = SdkDataPlaneMigrationStore(database_path).authority_state()
        snapshot["authority"] = authority.value
        snapshot["authority_migration_id"] = authority_migration
    except Exception as error:  # noqa: BLE001 - a snapshot must not fail its caller
        snapshot["authority"] = "unreadable"
        snapshot["authority_error"] = type(error).__name__
    snapshot["migrations"] = migration_rows(database_path)
    return snapshot
