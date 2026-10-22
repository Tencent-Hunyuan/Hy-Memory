"""Final management schema: fresh homes omit legacy authority tables."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.memory_server.migrations import MIGRATIONS, fresh_management_ddl

LEGACY_TABLES_IN_DROP_ORDER = (
    "extraction_settings",
    "matter_sdk_notes",
    "matter_sdk_versions",
    "matter_generation_keys",
    "matter_model_cache",
    "matter_memory_dependencies",
    "matter_notes",
    "matter_versions",
    "matter_runs",
    "matters",
    "change_outbox",
    "session_inbox_batches",
    "session_inbox_cursors",
    "session_snapshots",
    "session_subscriptions",
    "tail_handoff",
    "retrieval_chunks",
    "retrieval_documents",
    "retrieval_build_maps",
    "retrieval_consumers",
    "retrieval_index_generations",
    "retrieval_index_state",
    "memory_deletions",
    "memory_index_syncs",
    "memory_task_sdk_receipts",
    "memory_task_extractions",
    "memory_task_outputs",
    "memory_task_contexts",
    "memory_task_inputs",
    "memory_tasks",
    "governed_memory_evidence",
    "governed_memory_mutations",
    "governed_memory_supersessions",
    "governed_memory_versions",
    "governed_memories_terms",
    "governed_memories_terms_meta",
    "governed_memories",
    "memory_write_job_evidence",
    "evidence_pending_memory_dispatch",
    "evidence_ingest_events",
    "evidence_ingest_journal_state",
    "evidence_origins",
    "evidence_versions",
    "evidence_repairs",
    "evidence",
    "memory_id_aliases",
    "raw_id_aliases",
    "sdk_data_plane_migration_items",
    "sdk_data_plane_migrations",
    "sdk_data_plane_cutover",
)


def bootstrap_fresh_management_schema(database_path: Path) -> bool:
    """Create only the target management root for a brand-new home."""
    if database_path.exists():
        return False
    database_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with main_database(database_path).write(autocommit=True) as database:
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("BEGIN IMMEDIATE")
        database.executescript(fresh_management_ddl())
        database.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?,?,?)",
            (MIGRATIONS[-1].version, "fresh_management_schema", now),
        )
        database.execute(
            "INSERT INTO memory_authority_state "
            "(singleton,authority,migration_id,updated_at) VALUES (1,'sdk',NULL,?)",
            (now,),
        )
        database.execute("COMMIT")
    return True


def legacy_tables_present(database_path: Path) -> tuple[str, ...]:
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        present = {
            str(row["name"])
            for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    return tuple(name for name in LEGACY_TABLES_IN_DROP_ORDER if name in present)


def drop_legacy_tables(database_path: Path) -> tuple[str, ...]:
    """Drop migrated legacy tables. Authority state and verified bundle survive."""
    present = legacy_tables_present(database_path)
    if not present:
        return ()
    with main_database(database_path).write(autocommit=True) as database:
        database.execute("BEGIN IMMEDIATE")
        for table in present:
            database.execute(f'DROP TABLE IF EXISTS "{table}"')
        database.execute("COMMIT")
    return present
