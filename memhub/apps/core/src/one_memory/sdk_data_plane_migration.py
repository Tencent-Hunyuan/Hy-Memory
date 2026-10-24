"""Durable control-plane state for the SDK memory-authority cutover."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from one_memory.database_access import main_database


class MemoryAuthority(StrEnum):
    LEGACY = "legacy"
    SDK = "sdk"


class DataPlaneMigrationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CutoverMarker:
    migration_id: str
    sdk_version: str
    store_version: str
    namespace_fingerprint: str
    backend_fingerprint: str
    backup_bundle_id: str
    reconciliation_digest: str
    completed_at: str


@dataclass(frozen=True)
class MigrationProgress:
    migration_id: str
    state: str
    phase: str


class SdkDataPlaneMigrationStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def authority(self) -> MemoryAuthority:
        try:
            with main_database(self.database_path).read() as connection:
                row = connection.execute(
                    "SELECT authority FROM memory_authority_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error).lower():
                raise
        else:
            if row is not None:
                return MemoryAuthority(str(row[0]))
        return MemoryAuthority.SDK if self.cutover() is not None else MemoryAuthority.LEGACY

    def cutover(self) -> CutoverMarker | None:
        try:
            with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
                row = connection.execute(
                    "SELECT * FROM sdk_data_plane_cutover WHERE singleton=1"
                ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise
        if row is None:
            return None
        return CutoverMarker(
            migration_id=str(row["migration_id"]),
            sdk_version=str(row["sdk_version"]),
            store_version=str(row["store_version"]),
            namespace_fingerprint=str(row["namespace_fingerprint"]),
            backend_fingerprint=str(row["backend_fingerprint"]),
            backup_bundle_id=str(row["backup_bundle_id"]),
            reconciliation_digest=str(row["reconciliation_digest"]),
            completed_at=str(row["completed_at"]),
        )

    def start(
        self,
        *,
        migration_id: str,
        source_runtime_build: str,
        target_sdk_version: str,
        source_schema_version: int,
        namespace_fingerprint: str,
        backend_fingerprint: str,
        backup_bundle_id: str | None = None,
    ) -> MigrationProgress:
        now = self._now()
        values = (
            migration_id,
            "running",
            "preflight",
            source_runtime_build,
            target_sdk_version,
            source_schema_version,
            namespace_fingerprint,
            backend_fingerprint,
            backup_bundle_id,
            now,
            now,
        )
        with main_database(self.database_path).write(
            autocommit=True, row_factory=sqlite3.Row
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM sdk_data_plane_migrations WHERE migration_id=?",
                (migration_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sdk_data_plane_migrations (
                        migration_id,state,phase,source_runtime_build,
                        target_sdk_version,source_schema_version,
                        namespace_fingerprint,backend_fingerprint,backup_bundle_id,
                        created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
            elif backup_bundle_id and existing["backup_bundle_id"] is None:
                connection.execute(
                    "UPDATE sdk_data_plane_migrations "
                    "SET backup_bundle_id=?,updated_at=? WHERE migration_id=?",
                    (backup_bundle_id, now, migration_id),
                )
            elif backup_bundle_id and existing["backup_bundle_id"] != backup_bundle_id:
                raise DataPlaneMigrationError("sdk_data_plane_migration_bundle_conflict")
            connection.execute("COMMIT")
        return self.progress(migration_id)

    def set_phase(self, migration_id: str, *, phase: str, state: str = "running") -> None:
        with main_database(self.database_path).write(autocommit=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE sdk_data_plane_migrations SET phase=?,state=?,updated_at=? "
                "WHERE migration_id=? AND cutover_at IS NULL",
                (phase, state, self._now(), migration_id),
            )
            if cursor.rowcount != 1:
                raise DataPlaneMigrationError("sdk_data_plane_migration_not_mutable")
            connection.execute("COMMIT")

    def progress(self, migration_id: str) -> MigrationProgress:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            migration = connection.execute(
                "SELECT state,phase FROM sdk_data_plane_migrations WHERE migration_id=?",
                (migration_id,),
            ).fetchone()
        if migration is None:
            raise DataPlaneMigrationError("sdk_data_plane_migration_not_found")
        return MigrationProgress(
            migration_id=migration_id,
            state=str(migration["state"]),
            phase=str(migration["phase"]),
        )

    def commit_cutover(
        self,
        *,
        migration_id: str,
        sdk_version: str,
        store_version: str,
        backup_bundle_id: str,
        reconciliation_digest: str,
    ) -> CutoverMarker:
        now = self._now()
        with main_database(self.database_path).write(
            autocommit=True, row_factory=sqlite3.Row
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            migration = connection.execute(
                "SELECT * FROM sdk_data_plane_migrations WHERE migration_id=?",
                (migration_id,),
            ).fetchone()
            if migration is None:
                raise DataPlaneMigrationError("sdk_data_plane_migration_not_found")
            if migration["phase"] != "prepared" or migration["state"] != "ready":
                raise DataPlaneMigrationError("sdk_data_plane_migration_not_ready")
            if migration["backup_bundle_id"] != backup_bundle_id:
                raise DataPlaneMigrationError("sdk_data_plane_migration_bundle_conflict")
            existing = connection.execute(
                "SELECT * FROM sdk_data_plane_cutover WHERE singleton=1"
            ).fetchone()
            marker_values = (
                migration_id,
                sdk_version,
                store_version,
                str(migration["namespace_fingerprint"]),
                str(migration["backend_fingerprint"]),
                backup_bundle_id,
                reconciliation_digest,
            )
            if existing is not None:
                if marker_values != tuple(
                    existing[key]
                    for key in (
                        "migration_id",
                        "sdk_version",
                        "store_version",
                        "namespace_fingerprint",
                        "backend_fingerprint",
                        "backup_bundle_id",
                        "reconciliation_digest",
                    )
                ):
                    raise DataPlaneMigrationError("sdk_data_plane_cutover_conflict")
            else:
                connection.execute(
                    """
                    INSERT INTO sdk_data_plane_cutover (
                        singleton,migration_id,sdk_version,store_version,
                        namespace_fingerprint,backend_fingerprint,backup_bundle_id,
                        reconciliation_digest,completed_at
                    ) VALUES (1,?,?,?,?,?,?,?,?)
                    """,
                    (*marker_values, now),
                )
                connection.execute(
                    "UPDATE sdk_data_plane_migrations "
                    "SET state='cutover',phase='complete',"
                    "reconciliation_digest=?,cutover_at=?,updated_at=? "
                    "WHERE migration_id=?",
                    (reconciliation_digest, now, now, migration_id),
                )
            connection.execute(
                "INSERT INTO memory_authority_state "
                "(singleton,authority,migration_id,updated_at) "
                "VALUES (1,'sdk',?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET "
                "authority='sdk',migration_id=excluded.migration_id,"
                "updated_at=excluded.updated_at",
                (migration_id, now),
            )
            connection.execute("COMMIT")
        marker = self.cutover()
        if marker is None:
            raise DataPlaneMigrationError("sdk_data_plane_cutover_missing")
        return marker
