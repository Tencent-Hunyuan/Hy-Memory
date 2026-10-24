"""Read legacy memory IDs back through the SDK before (and after) the cutover.

The cutover is only safe if the SDK, configured exactly as the new Runtime
will configure it, returns the memories the legacy mirror lists. Checking that
the backup bundle is complete says nothing about it: build 20 dropped the
legacy tables while its Runtime read an empty vector collection.

Reads go through the public `HyMemoryClient` (`list_memories`, `get`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from one_memory.database_access import main_database
from one_memory.model_profiles import ModelProfileSnapshot
from one_memory.sdk_data_plane_migration import DataPlaneMigrationError

_PRODUCTIVITY_KINDS = frozenset({"coding", "working"})
_PRODUCTIVITY_LAYERS = frozenset(
    {"coding_experience", "working_experience", "coding_fact", "working_fact"}
)
_SDK_STATUSES = ("active", "superseded", "shadow", "negated", "conflicted", "archived")
_PAGE = 500


@dataclass(frozen=True)
class LegacyMemoryIds:
    chat: tuple[str, ...]
    productivity: tuple[str, ...]
    users: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.chat and not self.productivity


@dataclass(frozen=True)
class ReadbackReport:
    chat_expected: int
    chat_readable: int
    productivity_expected: int
    productivity_readable: int

    @property
    def passed(self) -> bool:
        # Every chat memory is an SDK node by construction, so all must read
        # back. The SDK's Coding reconciler deletes experiences it retires, so
        # a partial productivity count is normal; none at all is not.
        return self.chat_readable == self.chat_expected and (
            self.productivity_expected == 0 or self.productivity_readable > 0
        )

    def as_document(self) -> dict[str, object]:
        return {
            "chat_expected": self.chat_expected,
            "chat_readable": self.chat_readable,
            "productivity_expected": self.productivity_expected,
            "productivity_readable": self.productivity_readable,
            "passed": self.passed,
        }


class MemoryReadbackFailed(DataPlaneMigrationError):
    def __init__(self, report: ReadbackReport) -> None:
        super().__init__("sdk_migration_memory_unreadable")
        self.report = report


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def read_legacy_memory_ids(connection: sqlite3.Connection) -> LegacyMemoryIds:
    """Confirmed legacy memories, by the SDK id the old bridge recorded."""
    tables = _tables(connection)
    if "governed_memories" not in tables:
        return LegacyMemoryIds(chat=(), productivity=(), users=())
    mapped: dict[str, tuple[str, str | None]] = {}
    if "memory_task_outputs" in tables:
        for (body,) in connection.execute("SELECT body FROM memory_task_outputs"):
            try:
                document = json.loads(body)
            except (TypeError, ValueError):
                continue
            legacy_id, sdk_id = document.get("memory_id"), document.get("hy_memory_id")
            if isinstance(legacy_id, str) and isinstance(sdk_id, str):
                layer = document.get("layer")
                mapped[legacy_id] = (sdk_id, layer if isinstance(layer, str) else None)
    chat: set[str] = set()
    productivity: set[str] = set()
    users: set[str] = set()
    for memory_id, user_id, kind in connection.execute(
        "SELECT memory_id,user_id,kind FROM governed_memories WHERE status='confirmed'"
    ):
        sdk_id, layer = mapped.get(str(memory_id), (str(memory_id), None))
        users.add(str(user_id))
        if str(kind) in _PRODUCTIVITY_KINDS or layer in _PRODUCTIVITY_LAYERS:
            productivity.add(sdk_id)
        else:
            chat.add(sdk_id)
    return LegacyMemoryIds(
        chat=tuple(sorted(chat)),
        productivity=tuple(sorted(productivity)),
        users=tuple(sorted(users)),
    )


def legacy_memory_ids_from(database_path: Path) -> LegacyMemoryIds:
    with main_database(database_path).read() as connection:
        return read_legacy_memory_ids(connection)


def legacy_memory_ids_from_bundle(
    data_dir: Path, migration_id: str | None
) -> LegacyMemoryIds | None:
    """The same list from the pre-cutover bundle, for a home already migrated."""
    if not migration_id:
        return None
    database = (
        data_dir
        / "backups"
        / f"{migration_id}-pre-cutover.sdk-bundle"
        / "management"
        / "one-memory.sqlite3"
    )
    if not database.is_file():
        return None
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return read_legacy_memory_ids(connection)


def read_active_profile(database_path: Path) -> ModelProfileSnapshot | None:
    """The active model profile, read only; the status probe runs beside Core."""
    with main_database(database_path).read(row_factory=sqlite3.Row) as connection:
        if not {"model_profiles", "model_profile_versions"} <= _tables(connection):
            return None
        row = connection.execute(
            "SELECT version.* FROM model_profiles profile "
            "JOIN model_profile_versions version "
            "ON version.profile_id=profile.profile_id AND version.revision=profile.revision "
            "WHERE profile.is_active=1"
        ).fetchone()
    if row is None:
        return None
    return ModelProfileSnapshot(
        profile_id=row["profile_id"],
        revision=row["revision"],
        provider_kind=row["provider_kind"],
        base_url=row["base_url"],
        model=row["model"],
        embedding_model=row["embedding_model"],
        embedding_provider_kind=row["embedding_provider_kind"],
        embedding_base_url=row["embedding_base_url"],
        embedding_dimensions=row["embedding_dimensions"],
        timeout_seconds=row["timeout_seconds"],
        max_concurrency=row["max_concurrency"],
        context_window=row["context_window"],
        has_api_key=row["secret_ref"] is not None,
        disable_thinking=bool(row["disable_thinking"]),
    )


@contextmanager
def runtime_sdk_client(
    database_path: Path, data_dir: Path, profile: ModelProfileSnapshot
) -> Iterator[Any]:
    """A public SDK client configured exactly as the Runtime configures it."""
    from one_memory.hy_sdk_runtime import acquire_client
    from one_memory.model_profiles import ModelProfileStore
    from one_memory.secrets import KeyringSecretStore, SecretStoreUnavailableError

    try:
        secret = ModelProfileStore(database_path).resolve_snapshot_secret(
            profile.profile_id, profile.revision, KeyringSecretStore()
        )
    except SecretStoreUnavailableError:
        raise DataPlaneMigrationError("sdk_migration_secure_storage_unavailable") from None
    if profile.has_api_key and secret is None:
        raise DataPlaneMigrationError("sdk_migration_model_secret_unavailable")
    lease = acquire_client(profile, api_key=secret, data_dir=data_dir)
    try:
        yield lease.client
    finally:
        lease.close()


def verify_readback(client: Any, legacy: LegacyMemoryIds) -> ReadbackReport:
    listed: set[str] = set()
    for user_id in legacy.users or ("local",):
        offset = 0
        while True:
            raw = client.list_memories(
                user_id=user_id,
                statuses=list(_SDK_STATUSES),
                limit=_PAGE,
                offset=offset,
                order="asc",
            )
            bucket = raw.get("vdb") if isinstance(raw, dict) else None
            rows = bucket.get("memories") if isinstance(bucket, dict) else None
            total = bucket.get("total") if isinstance(bucket, dict) else None
            if not isinstance(rows, list) or not isinstance(total, int):
                raise DataPlaneMigrationError("sdk_migration_memory_list_shape_unknown")
            listed.update(
                str(row["memory_id"])
                for row in rows
                if isinstance(row, dict) and row.get("memory_id")
            )
            offset += len(rows)
            if not rows or offset >= total:
                break

    def readable(memory_id: str) -> bool:
        return memory_id in listed or client.get(memory_id) is not None

    return ReadbackReport(
        chat_expected=len(legacy.chat),
        chat_readable=sum(1 for memory_id in legacy.chat if readable(memory_id)),
        productivity_expected=len(legacy.productivity),
        productivity_readable=sum(1 for memory_id in legacy.productivity if readable(memory_id)),
    )

