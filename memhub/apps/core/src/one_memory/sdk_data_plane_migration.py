"""Durable control-plane state for the SDK memory-authority cutover."""

from __future__ import annotations

import json
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
    counts: dict[str, int]


TERMINAL_ITEM_STATES = frozenset({"verified", "adopted", "deleted", "skipped"})


class SdkDataPlaneMigrationStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def authority(self) -> MemoryAuthority:
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
        source_sdk_version: str,
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
            source_sdk_version,
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
                        source_sdk_version,target_sdk_version,source_schema_version,
                        namespace_fingerprint,backend_fingerprint,backup_bundle_id,
                        created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
            else:
                identity = (
                    source_runtime_build,
                    source_sdk_version,
                    target_sdk_version,
                    source_schema_version,
                    namespace_fingerprint,
                    backend_fingerprint,
                )
                existing_identity = tuple(
                    existing[key]
                    for key in (
                        "source_runtime_build",
                        "source_sdk_version",
                        "target_sdk_version",
                        "source_schema_version",
                        "namespace_fingerprint",
                        "backend_fingerprint",
                    )
                )
                if existing_identity != identity:
                    raise DataPlaneMigrationError("sdk_data_plane_migration_identity_conflict")
                if backup_bundle_id and existing["backup_bundle_id"] not in {
                    None,
                    backup_bundle_id,
                }:
                    raise DataPlaneMigrationError("sdk_data_plane_migration_bundle_conflict")
                if backup_bundle_id and existing["backup_bundle_id"] is None:
                    connection.execute(
                        "UPDATE sdk_data_plane_migrations "
                        "SET backup_bundle_id=?,updated_at=? WHERE migration_id=?",
                        (backup_bundle_id, now, migration_id),
                    )
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

    def upsert_item(
        self,
        *,
        migration_id: str,
        item_key: str,
        source_kind: str,
        source_id: str,
        source_hash: str,
        operation_key: str,
        source_revision: str | None = None,
        state: str = "pending",
    ) -> None:
        now = self._now()
        with main_database(self.database_path).write(
            autocommit=True, row_factory=sqlite3.Row
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM sdk_data_plane_migration_items "
                "WHERE migration_id=? AND item_key=?",
                (migration_id, item_key),
            ).fetchone()
            if row is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO sdk_data_plane_migration_items (
                            migration_id,item_key,source_kind,source_id,
                            source_revision,source_hash,operation_key,state,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            migration_id,
                            item_key,
                            source_kind,
                            source_id,
                            source_revision,
                            source_hash,
                            operation_key,
                            state,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise DataPlaneMigrationError(
                        "sdk_data_plane_migration_operation_conflict"
                    ) from error
            else:
                identity = (
                    source_kind,
                    source_id,
                    source_revision,
                    source_hash,
                    operation_key,
                )
                if identity != tuple(
                    row[key]
                    for key in (
                        "source_kind",
                        "source_id",
                        "source_revision",
                        "source_hash",
                        "operation_key",
                    )
                ):
                    raise DataPlaneMigrationError("sdk_data_plane_migration_item_conflict")
            connection.execute("COMMIT")

    def settle_item(
        self,
        *,
        migration_id: str,
        item_key: str,
        state: str,
        target_memory_id: str | None = None,
        target_hash: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with main_database(self.database_path).write(autocommit=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE sdk_data_plane_migration_items
                SET state=?,target_memory_id=?,target_hash=?,error_code=?,
                    attempts=attempts+1,updated_at=?
                WHERE migration_id=? AND item_key=?
                """,
                (
                    state,
                    target_memory_id,
                    target_hash,
                    error_code,
                    self._now(),
                    migration_id,
                    item_key,
                ),
            )
            if cursor.rowcount != 1:
                raise DataPlaneMigrationError("sdk_data_plane_migration_item_not_found")
            connection.execute("COMMIT")

    def record_alias(
        self,
        *,
        migration_id: str,
        legacy_memory_id: str,
        canonical_memory_id: str,
    ) -> None:
        with main_database(self.database_path).write(
            autocommit=True, row_factory=sqlite3.Row
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT canonical_memory_id,migration_id FROM memory_id_aliases "
                "WHERE legacy_memory_id=?",
                (legacy_memory_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO memory_id_aliases (
                        legacy_memory_id,canonical_memory_id,migration_id,created_at
                    ) VALUES (?,?,?,?)
                    """,
                    (
                        legacy_memory_id,
                        canonical_memory_id,
                        migration_id,
                        self._now(),
                    ),
                )
            elif (
                existing["canonical_memory_id"] != canonical_memory_id
                or existing["migration_id"] != migration_id
            ):
                raise DataPlaneMigrationError("sdk_data_plane_alias_conflict")
            connection.execute("COMMIT")

    def progress(self, migration_id: str) -> MigrationProgress:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as connection:
            migration = connection.execute(
                "SELECT state,phase FROM sdk_data_plane_migrations WHERE migration_id=?",
                (migration_id,),
            ).fetchone()
            if migration is None:
                raise DataPlaneMigrationError("sdk_data_plane_migration_not_found")
            counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state,COUNT(*) AS count "
                    "FROM sdk_data_plane_migration_items WHERE migration_id=? GROUP BY state",
                    (migration_id,),
                )
            }
        return MigrationProgress(
            migration_id=migration_id,
            state=str(migration["state"]),
            phase=str(migration["phase"]),
            counts=counts,
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
            if migration["phase"] != "reconciled" or migration["state"] != "ready":
                raise DataPlaneMigrationError("sdk_data_plane_migration_not_ready")
            unresolved = connection.execute(
                "SELECT state,COUNT(*) AS count FROM sdk_data_plane_migration_items "
                "WHERE migration_id=? GROUP BY state",
                (migration_id,),
            ).fetchall()
            blocked = {
                str(row["state"]): int(row["count"])
                for row in unresolved
                if str(row["state"]) not in TERMINAL_ITEM_STATES
            }
            if blocked:
                raise DataPlaneMigrationError(
                    "sdk_data_plane_migration_items_unresolved:"
                    + json.dumps(blocked, sort_keys=True, separators=(",", ":"))
                )
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
            connection.execute("COMMIT")
        marker = self.cutover()
        if marker is None:
            raise DataPlaneMigrationError("sdk_data_plane_cutover_missing")
        return marker
