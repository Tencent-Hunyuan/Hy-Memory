"""Host-recall ACL metadata keyed only by SDK memory_id."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import main_database


class MemoryAccessScopeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def replace(self, memory_id: str, scopes: tuple[str, ...]) -> None:
        cleaned = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
        if not memory_id or not cleaned:
            raise ValueError("memory_id and at least one scope are required")
        now = datetime.now(UTC).isoformat()
        with main_database(self.database_path).write(autocommit=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM memory_access_scopes WHERE memory_id=?",
                (memory_id,),
            )
            connection.executemany(
                "INSERT INTO memory_access_scopes(memory_id,scope,created_at) "
                "VALUES (?,?,?)",
                [(memory_id, scope, now) for scope in cleaned],
            )
            connection.execute("COMMIT")

    def remove(self, memory_id: str) -> None:
        with main_database(self.database_path).write(autocommit=True) as connection:
            connection.execute(
                "DELETE FROM memory_access_scopes WHERE memory_id=?",
                (memory_id,),
            )

    def by_memory_ids(
        self,
        memory_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        ids = tuple(dict.fromkeys(value for value in memory_ids if value))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            rows = connection.execute(
                f"SELECT memory_id,scope FROM memory_access_scopes "
                f"WHERE memory_id IN ({placeholders}) ORDER BY memory_id,scope",
                ids,
            ).fetchall()
        grouped: dict[str, list[str]] = {memory_id: [] for memory_id in ids}
        for row in rows:
            grouped[str(row["memory_id"])].append(str(row["scope"]))
        return {key: tuple(value) for key, value in grouped.items()}

    def readable(
        self,
        memory_ids: tuple[str, ...],
        scopes: frozenset[str] | None,
    ) -> frozenset[str]:
        mapped = self.by_memory_ids(memory_ids)
        if scopes is None:
            return frozenset(
                memory_id for memory_id, allowed in mapped.items() if allowed
            )
        return frozenset(
            memory_id
            for memory_id, allowed in mapped.items()
            if not scopes.isdisjoint(allowed)
        )
