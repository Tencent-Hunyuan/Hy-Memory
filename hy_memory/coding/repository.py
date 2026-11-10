"""Business repository selection, independent of vector providers."""

import os

from ..config import MemoryConfig
from .repository_base import MemoryRepository


def create_memory_repository(
    config: MemoryConfig, db_path: str, trace_db_path: str, variant: str,
) -> MemoryRepository:
    settings = config.business_store
    settings.validate()
    if settings.backend == "postgres":
        from .repository_postgres import PostgresMemoryRepository
        return PostgresMemoryRepository(settings, variant)
    from .repository_sqlite import SqliteMemoryRepository
    return SqliteMemoryRepository(db_path, trace_db_path)


def memory_repository_exists(config: MemoryConfig, db_path: str, variant: str) -> bool:
    """Probe a namespace without creating schemas, files or vector collections."""
    config.business_store.validate()
    if config.business_store.backend == "postgres":
        from .repository_postgres import postgres_namespace_exists
        return postgres_namespace_exists(config.business_store, variant)
    return os.path.isfile(db_path)
