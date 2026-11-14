"""Optional PostgreSQL authoritative business storage (psycopg 3)."""

from contextlib import contextmanager
from typing import Iterable, Iterator, Tuple

from ..config import BusinessStoreConfig
from .relational_repository import RelationalMemoryRepository
from .repository_schema import (
    _CREATE_TABLE_SQL, _CREATE_FACT_TABLE_SQL, _CREATE_TRACE_TABLE_SQL, _CREATE_INDEX_SQL,
    _CREATE_CONSOLIDATION_SQL,
)

SCHEMA_VERSION = 2


def connect_postgres(settings: BusinessStoreConfig, *, mapping_rows: bool = True):
    settings.validate()
    if not settings.postgres_dsn or not settings.postgres_dsn.strip():
        raise ValueError("PostgreSQL business storage requires MEMORY_POSTGRES_DSN")
    try:
        import psycopg
        from psycopg.rows import dict_row, tuple_row
    except ImportError as error:
        raise ImportError('PostgreSQL storage requires pip install "hy-memory[postgres]"') from error
    # Autocommit prevents reads from keeping idle transactions open. Multi-step
    # changes use explicit transaction blocks, which roll back on exceptions.
    return psycopg.connect(
        settings.postgres_dsn, autocommit=True,
        connect_timeout=settings.connect_timeout,
        row_factory=dict_row if mapping_rows else tuple_row,
    )


def namespace(settings: BusinessStoreConfig, kind: str) -> str:
    settings.validate()
    if kind not in ("working", "coding", "trace", "chain"):
        raise ValueError("Unknown business storage namespace")
    return f"{settings.postgres_schema}_{kind}"


@contextmanager
def initialize_namespace(connection, schema: str):
    """Serialize schema upgrades across processes; failed DDL leaves no half-schema."""
    from psycopg import sql

    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (schema,))
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        connection.execute(
            "CREATE TABLE IF NOT EXISTS business_schema_version "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
        )
        row = connection.execute("SELECT version FROM business_schema_version WHERE id=1").fetchone()
        version = (row["version"] if isinstance(row, dict) else row[0]) if row else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError("PostgreSQL business schema is newer than this SDK")
        yield version
        connection.execute(
            "INSERT INTO business_schema_version(id,version) VALUES(1,%s) "
            "ON CONFLICT(id) DO UPDATE SET version=excluded.version", (SCHEMA_VERSION,),
        )


def execute_schema(connection, script: str) -> None:
    # SDK-owned DDL has no function bodies or semicolons in literals. Execute
    # statements separately so parameter protocol/multi-result behavior is explicit.
    script = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("--"))
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


def postgres_namespace_exists(settings: BusinessStoreConfig, variant: str) -> bool:
    schema = namespace(settings, variant)
    with connect_postgres(settings) as connection:
        return connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s "
            "AND table_name='coding_memory_meta'", (schema,),
        ).fetchone() is not None


class PostgresMemoryRepository(RelationalMemoryRepository):
    def __init__(self, settings: BusinessStoreConfig, variant: str) -> None:
        super().__init__()
        self.settings = settings
        self.schema = namespace(settings, variant)
        self.trace_schema = namespace(settings, "trace")

    def initialize(self) -> None:
        try:
            self._conn = connect_postgres(self.settings)
            with initialize_namespace(self._conn, self.schema) as version:
                # Epoch seconds need BIGINT beyond 2038; confidence must retain
                # SQLite's double precision rather than PostgreSQL REAL precision.
                ddl = (_CREATE_TABLE_SQL + _CREATE_FACT_TABLE_SQL).replace(
                    "memory_at        INTEGER", "memory_at        BIGINT"
                ).replace("memory_at              INTEGER", "memory_at              BIGINT")
                execute_schema(self._conn, ddl.replace("REAL", "DOUBLE PRECISION"))
                if version < 2:
                    self._conn.execute("ALTER TABLE coding_memory_meta ADD COLUMN IF NOT EXISTS consolidation_id TEXT")
                    self._conn.execute("ALTER TABLE coding_memory_meta ADD COLUMN IF NOT EXISTS evidence_refs TEXT NOT NULL DEFAULT '[]'")
                    execute_schema(self._conn, _CREATE_CONSOLIDATION_SQL)
                for statement in _CREATE_INDEX_SQL:
                    self._conn.execute(statement)
            self._trace_conn = connect_postgres(self.settings)
            with initialize_namespace(self._trace_conn, self.trace_schema):
                execute_schema(self._trace_conn, _CREATE_TRACE_TABLE_SQL)
        except BaseException:
            self.close()
            raise

    def _execute(self, sql: str, params=(), *, trace: bool = False):
        # Only SDK-owned query templates contain '?'; data always stays bound.
        # No SQLite DDL or runtime query emulation belongs in the business facade.
        return super()._execute(sql.replace("?", "%s"), params, trace=trace)

    @contextmanager
    def _business_transaction(self, *, write: bool = True) -> Iterator[None]:
        from .types import ConsolidationOutcomeUnknown
        transaction = self._conn.transaction()
        transaction.__enter__()
        try:
            if not write:
                self._conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield
        except BaseException:
            import sys
            transaction.__exit__(*sys.exc_info())
            raise
        try:
            transaction.__exit__(None, None, None)
        except BaseException as error:
            raise ConsolidationOutcomeUnknown("PostgreSQL commit acknowledgement lost") from error

    def _lock_sources(self, refs: Iterable[Tuple[str, str]]) -> None:
        for kind, key in sorted(set(refs)):
            # Key locks also cover deletion/reinsertion, for which no row exists.
            self._conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                               (self.schema, f"source:{kind}:{key}"))
            table, column = (("working_fact", "fact_id") if kind == "fact"
                             else ("coding_memory_meta", "experience_id"))
            self._conn.execute(f"SELECT {column} FROM {table} WHERE {column}=%s FOR UPDATE", (key,))

    def _lock_groups(self, groups: Iterable[str]) -> None:
        for group in sorted(set(groups)):
            self._conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                               (self.schema, f"group:{group}"))

    @contextmanager
    def _trace_transaction(self, user_id: str, agent_id: str):
        with self._trace_conn.transaction():
            self._trace_conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                (self.trace_schema, f"{len(user_id)}:{user_id}{agent_id or 'default_agent'}"),
            )
            yield
