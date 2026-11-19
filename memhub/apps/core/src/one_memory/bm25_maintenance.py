"""Explicit stopped-writer maintenance for the live Qdrant memory collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from one_memory.embedding_spaces import EmbeddingSpaceStore
from one_memory.hy_sdk_runtime import (
    acquire_client,
    builtin_retrieval_snapshot,
    retrieval_view,
)
from one_memory.memory_server.config_state import ConfigStore
from one_memory.model_profiles import ModelProfileStore
from one_memory.runtime_client import inspect_runtime
from one_memory.secrets import KeyringSecretStore
from one_memory.storage_backends import StorageBackendStore

SCHEMA_VERSION = 1
PRODUCT = "memhub-qdrant-bm25-maintenance"


class Bm25MaintenanceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def maintain_bm25(
    data_dir: Path,
    *,
    execute: bool,
    batch_size: int = 256,
) -> dict[str, Any]:
    """Plan or execute the SDK's native Qdrant BM25 upgrade for the live space."""
    if batch_size < 1 or batch_size > 10_000:
        raise Bm25MaintenanceError("bm25_maintenance_batch_size_invalid")

    runtime = inspect_runtime(data_dir)
    if runtime.state != "stopped":
        return _blocked(
            "bm25_maintenance_runtime_must_be_stopped",
            runtime_state=runtime.state,
            runtime_code=runtime.code,
        )

    database = data_dir / "one-memory.sqlite3"
    if not database.is_file():
        return _not_applicable("bm25_maintenance_home_not_initialized")

    secrets = KeyringSecretStore()
    storage = StorageBackendStore(ConfigStore(data_dir), secrets).resolved()
    if storage is None or storage.vector_provider != "qdrant":
        return _not_applicable("bm25_maintenance_vector_store_not_qdrant")

    profiles = ModelProfileStore(database)
    spaces = EmbeddingSpaceStore(database)
    space = spaces.live()
    if space is None:
        active = profiles.active(secrets)
        snapshot = (
            profiles.snapshot(active.profile_id, active.revision)
            if active is not None
            else builtin_retrieval_snapshot()
        )
        space = spaces.bootstrap(snapshot)
    elif space.profile_id is None or space.profile_revision is None:
        snapshot = builtin_retrieval_snapshot()
    else:
        snapshot = profiles.snapshot(space.profile_id, space.profile_revision)

    api_key = (
        profiles.resolve_snapshot_secret(
            space.profile_id,
            space.profile_revision,
            secrets,
        )
        if space.profile_id is not None and space.profile_revision is not None
        else None
    )
    with acquire_client(
        retrieval_view(snapshot),
        space,
        api_key=api_key,
        data_dir=data_dir,
        mode="lite",
        storage=storage,
    ) as lease:
        if execute:
            result = lease.client.upgrade_qdrant_bm25(
                writers_stopped=True,
                batch_size=batch_size,
            )
        else:
            result = lease.client.plan_qdrant_bm25_upgrade()

    # MemHub envelope fields come last so the SDK's product/schema keys cannot
    # overwrite the maintenance CLI contract.
    return {
        **dict(result),
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "operation": "upgrade" if execute else "plan",
        "space_id": space.space_id,
        "collection_base": space.collection_base,
    }


def _blocked(reason: str, **details: object) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "state": "blocked",
        "reason": reason,
        "success": False,
        "changed": False,
        **details,
    }


def _not_applicable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "state": "not_applicable",
        "reason": reason,
        "success": False,
        "changed": False,
    }
