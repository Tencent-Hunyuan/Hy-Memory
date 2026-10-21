"""Management-only handoff from exact SDK raw import to durable scheduling."""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from one_memory.database_access import main_database


@dataclass(frozen=True)
class SdkRawDispatch:
    user_id: str
    source_id: str
    file_key: str
    internal_session_id: str
    workspace: str | None
    agent_id: str
    raw_ids: tuple[str, ...]
    priority: Literal["normal", "live"]
    event_at: datetime | None


class SdkRawDispatchConflict(RuntimeError):
    pass


class SdkRawDispatchStore:
    """Durable raw-ID receipts. This store never accepts or returns raw content."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def stage(
        self,
        *,
        user_id: str,
        source_id: str,
        file_key: str,
        internal_session_id: str,
        workspace: str | None,
        agent_id: str,
        raw_id: str,
        source_order: int,
        priority: Literal["normal", "live"],
        event_at: datetime | None,
        import_request_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        values = (
            user_id,
            source_id,
            file_key,
            internal_session_id,
            workspace,
            agent_id,
            raw_id,
            source_order,
            priority,
            event_at.isoformat() if event_at is not None else None,
            import_request_id,
        )
        if (
            not all(
                isinstance(value, str) and value
                for value in (
                    user_id,
                    source_id,
                    file_key,
                    internal_session_id,
                    agent_id,
                    raw_id,
                    import_request_id,
                )
            )
            or type(source_order) is not int
            or source_order < 0
        ):
            raise ValueError("invalid SDK raw dispatch identity")
        context = (
            nullcontext(connection)
            if connection is not None
            else main_database(self.database_path).write(
                autocommit=True,
                row_factory=sqlite3.Row,
            )
        )
        with context as database:
            assert database is not None
            existing = database.execute(
                "SELECT user_id,source_id,file_key,internal_session_id,workspace,"
                "agent_id,raw_id,source_order,event_at,import_request_id,priority "
                "FROM sdk_raw_pending_dispatch "
                "WHERE user_id=? AND source_id=? AND file_key=? AND raw_id=?",
                (user_id, source_id, file_key, raw_id),
            ).fetchone()
            if existing is not None:
                immutable = tuple(existing[key] for key in existing if key != "priority")
                expected = (*values[:8], values[9], values[10])
                if immutable != expected:
                    raise SdkRawDispatchConflict("sdk_raw_dispatch_identity_conflict")
                if existing["priority"] == "normal" and priority == "live":
                    database.execute(
                        "UPDATE sdk_raw_pending_dispatch SET priority='live' "
                        "WHERE user_id=? AND source_id=? AND file_key=? AND raw_id=?",
                        (user_id, source_id, file_key, raw_id),
                    )
                return
            database.execute(
                "INSERT INTO sdk_raw_pending_dispatch "
                "(user_id,source_id,file_key,internal_session_id,workspace,agent_id,"
                "raw_id,source_order,priority,event_at,import_request_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values, datetime.now(UTC).isoformat()),
            )

    def pending_files(self, *, limit: int = 10_000) -> tuple[tuple[str, str, str], ...]:
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            rows = database.execute(
                "SELECT user_id,source_id,file_key FROM sdk_raw_pending_dispatch "
                "GROUP BY user_id,source_id,file_key "
                "ORDER BY user_id,source_id,file_key LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            (str(row["user_id"]), str(row["source_id"]), str(row["file_key"]))
            for row in rows
        )

    def take_file(
        self,
        *,
        user_id: str,
        source_id: str,
        file_key: str,
    ) -> tuple[SdkRawDispatch, ...]:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            rows = database.execute(
                "SELECT * FROM sdk_raw_pending_dispatch "
                "WHERE user_id=? AND source_id=? AND file_key=? "
                "ORDER BY internal_session_id,workspace,source_order,raw_id",
                (user_id, source_id, file_key),
            ).fetchall()
        grouped: dict[tuple[str, str | None, str], list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(
                (
                    str(row["internal_session_id"]),
                    str(row["workspace"]) if row["workspace"] is not None else None,
                    str(row["agent_id"]),
                ),
                [],
            ).append(row)
        result = []
        for (session_id, workspace, agent_id), items in grouped.items():
            event_values = [
                datetime.fromisoformat(str(row["event_at"]))
                for row in items
                if row["event_at"] is not None
            ]
            result.append(
                SdkRawDispatch(
                    user_id=user_id,
                    source_id=source_id,
                    file_key=file_key,
                    internal_session_id=session_id,
                    workspace=workspace,
                    agent_id=agent_id,
                    raw_ids=tuple(str(row["raw_id"]) for row in items),
                    priority=(
                        "live"
                        if any(str(row["priority"]) == "live" for row in items)
                        else "normal"
                    ),
                    event_at=max(event_values, default=None),
                )
            )
        return tuple(result)

    def consume_file(self, *, user_id: str, source_id: str, file_key: str) -> int:
        with main_database(self.database_path).write(autocommit=True) as database:
            changed = database.execute(
                "DELETE FROM sdk_raw_pending_dispatch "
                "WHERE user_id=? AND source_id=? AND file_key=?",
                (user_id, source_id, file_key),
            )
        return changed.rowcount
