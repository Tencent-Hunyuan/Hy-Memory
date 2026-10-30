import ipaddress
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypedDict, cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database
from one_memory.memory_server.migrations import (
    model_profiles_ddl,
    profile_creation_operations_ddl,
)
from one_memory.secrets import SecretStore, SecretStoreUnavailableError


class ProviderKind(StrEnum):
    HY_DEFAULT = "hy_default"
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    LOCAL_ONNX = "local_onnx"


DEFAULT_BASE_URLS = {
    ProviderKind.OPENAI_COMPATIBLE: "https://api.openai.com/v1",
    ProviderKind.ANTHROPIC: "https://api.anthropic.com",
    ProviderKind.OLLAMA: "http://127.0.0.1:11434",
}

_PROFILE_SNAPSHOT_COLUMNS = (
    "profile_id, revision, provider_kind, base_url, model, embedding_model, "
    "embedding_provider_kind, embedding_base_url, embedding_dimensions, "
    "timeout_seconds, max_concurrency, context_window, disable_thinking, "
    "(secret_ref IS NOT NULL) AS has_api_key"
)


class ModelProfileInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    provider_kind: ProviderKind
    base_url: str | None = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=256)
    embedding_provider_kind: ProviderKind | None = None
    embedding_base_url: str | None = Field(default=None, max_length=2048)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=1_000_000)
    api_key: SecretStr | None = None
    creation_operation_id: str | None = Field(
        default=None, strict=True, min_length=36, max_length=36
    )
    # Measured on the extraction prompt: thinking mode is
    # 22.5s/1262 tokens and drops a fact, while a disabled chain is 1.8s/39
    # tokens and finds all of them. A first-run user has no reason to pay 12x
    # for a worse result, and a 60s ceiling is what turned that latency into
    # task failure rather than slowness.
    timeout_seconds: int = Field(default=240, ge=1, le=600)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    disable_thinking: bool = True

    @field_validator("creation_operation_id")
    @classmethod
    def validate_creation_operation(cls, value: str | None) -> str | None:
        if value is not None:
            _creation_operation_id(value)
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ModelProfileInput":
        base_url = self.base_url or DEFAULT_BASE_URLS.get(self.provider_kind)
        if self.provider_kind is ProviderKind.HY_DEFAULT:
            if self.base_url is not None:
                raise ValueError("HY default endpoint is managed by the product")
            return self
        if base_url is None:
            raise ValueError("base_url is required")
        validate_model_base_url(base_url)
        (
            embedding_provider_kind,
            embedding_model,
            embedding_base_url,
            _dimensions,
        ) = apply_builtin_embedding_defaults(
            embedding_model=self.embedding_model,
            embedding_provider_kind=self.embedding_provider_kind,
            embedding_base_url=self.embedding_base_url,
            embedding_dimensions=self.embedding_dimensions,
        )
        embedding_provider, resolved_embedding_base_url = resolve_embedding_endpoint(
            provider_kind=self.provider_kind,
            base_url=base_url,
            embedding_model=embedding_model,
            embedding_provider_kind=embedding_provider_kind,
            embedding_base_url=embedding_base_url,
        )
        if embedding_provider in {
            ProviderKind.HY_DEFAULT,
            ProviderKind.ANTHROPIC,
        }:
            raise ValueError("embedding provider must be OpenAI-compatible, Ollama, or local ONNX")
        if (
            embedding_provider is not ProviderKind.LOCAL_ONNX
            and resolved_embedding_base_url is not None
        ):
            validate_model_base_url(resolved_embedding_base_url)
        validate_embedding_credential_boundary(
            provider_kind=self.provider_kind,
            base_url=base_url,
            embedding_provider_kind=embedding_provider,
            embedding_base_url=resolved_embedding_base_url,
        )
        return self


class ModelProfilePatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=256)
    embedding_provider_kind: ProviderKind | None = None
    embedding_base_url: str | None = Field(default=None, max_length=2048)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=1_000_000)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    disable_thinking: bool | None = None

    @model_validator(mode="after")
    def validate_secret_change(self) -> "ModelProfilePatch":
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_reachable: bool = False
    model_available: bool = False
    structured_output: bool = False
    embedding_available: bool = False


class ModelProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    name: str
    provider_kind: ProviderKind
    base_url: str | None
    model: str
    embedding_model: str | None = None
    embedding_provider_kind: ProviderKind | None = None
    embedding_base_url: str | None = None
    embedding_dimensions: int | None = None
    has_api_key: bool
    # Saved identity metadata, not proof that the credential can currently be read.
    has_configured_api_key: bool = False
    credential_state: Literal["available", "missing", "unavailable"] = "missing"
    timeout_seconds: int
    max_concurrency: int
    context_window: int | None
    disable_thinking: bool = True
    capabilities: ModelCapabilities
    revision: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ModelProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    revision: int
    provider_kind: ProviderKind
    base_url: str | None
    model: str
    embedding_model: str | None = None
    embedding_provider_kind: ProviderKind | None = None
    embedding_base_url: str | None = None
    embedding_dimensions: int | None = None
    timeout_seconds: int
    max_concurrency: int
    context_window: int | None
    has_api_key: bool
    # 与 ModelProfile 的默认值保持一致：忘记显式传参时不应悄悄打开思考链。
    disable_thinking: bool = True


class ModelProfileConflictError(RuntimeError):
    pass


class ModelCreationOperationConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("model_creation_operation_conflict")


class ModelCreationOperationLookupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("model_creation_operation_lookup_unavailable")


class ModelCreationOperationReceipt(TypedDict):
    operation_id: str
    state: Literal["committed", "pending", "deleted", "not_found"]
    profile_id: str | None
    created_revision: Literal[1] | None


def _creation_operation_id(value: str) -> None:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_creation_operation_id") from None


class ActiveModelProfileError(RuntimeError):
    pass


class ModelProfileUnavailableError(RuntimeError):
    pass


PROFILE_SCHEMA = model_profiles_ddl("sqlite")


class ModelProfileStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        with self._connect(writing=True) as connection:
            connection.executescript(PROFILE_SCHEMA)
            version_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_profile_versions)")
            }
            if "secret_ref" not in version_columns:
                connection.execute("ALTER TABLE model_profile_versions ADD COLUMN secret_ref TEXT")
                connection.execute(
                    """
                    UPDATE model_profile_versions
                    SET secret_ref = (
                        SELECT model_profiles.secret_ref
                        FROM model_profiles
                        WHERE model_profiles.profile_id =
                              model_profile_versions.profile_id
                    )
                    """
                )
            profile_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(model_profiles)")
            }
            for table, columns in (
                ("model_profiles", profile_columns),
                ("model_profile_versions", version_columns),
            ):
                if "embedding_model" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN embedding_model TEXT")
                if "embedding_provider_kind" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN embedding_provider_kind TEXT"
                    )
                if "embedding_base_url" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN embedding_base_url TEXT")
                if "embedding_dimensions" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN embedding_dimensions INTEGER"
                    )
            if "embedding_available" not in profile_columns:
                connection.execute(
                    "ALTER TABLE model_profiles "
                    "ADD COLUMN embedding_available INTEGER NOT NULL DEFAULT 0"
                )
            for table, columns in (
                ("model_profiles", profile_columns),
                ("model_profile_versions", version_columns),
            ):
                if "disable_thinking" not in columns:
                    # SQLite fills DEFAULT into every existing row, so this is
                    # also the migration for profiles created before the flag
                    # existed. Those predate the measurement that made the
                    # default wrong, so they are corrected here rather than
                    # preserved; a user who deliberately wants the chain on
                    # can still turn it back on per profile.
                    connection.execute(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN disable_thinking INTEGER NOT NULL DEFAULT 1"
                    )
            for table in ("model_profiles", "model_profile_versions"):
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET embedding_provider_kind = provider_kind,
                        embedding_base_url = base_url
                    WHERE embedding_model IS NOT NULL
                      AND embedding_provider_kind IS NULL
                    """
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO model_profile_versions (
                    profile_id, revision, provider_kind, base_url, model,
                    embedding_model, embedding_provider_kind, embedding_base_url,
                    embedding_dimensions,
                    timeout_seconds, max_concurrency, context_window,
                    disable_thinking,
                    secret_ref, created_at
                )
                SELECT profile_id, revision, provider_kind, base_url, model,
                       embedding_model, embedding_provider_kind, embedding_base_url,
                       embedding_dimensions,
                       timeout_seconds, max_concurrency, context_window,
                       disable_thinking,
                       secret_ref, updated_at
                FROM model_profiles
                """
            )

    def create(self, value: ModelProfileInput, secrets: SecretStore) -> ModelProfile:
        profile_id = str(uuid4())
        if value.creation_operation_id is not None:
            self._reserve_creation_operation(value.creation_operation_id, profile_id)
        secret_ref = f"model-profile:{profile_id}" if value.api_key is not None else None
        if secret_ref and value.api_key:
            secrets.put(secret_ref, value.api_key.get_secret_value())
        now = datetime.now(UTC).isoformat()
        base_url = value.base_url or DEFAULT_BASE_URLS.get(value.provider_kind)
        (
            defaulted_provider,
            defaulted_model,
            defaulted_base_url,
            defaulted_dimensions,
        ) = apply_builtin_embedding_defaults(
            embedding_model=value.embedding_model,
            embedding_provider_kind=value.embedding_provider_kind,
            embedding_base_url=value.embedding_base_url,
            embedding_dimensions=value.embedding_dimensions,
        )
        embedding_provider_kind, embedding_base_url = resolve_embedding_endpoint(
            provider_kind=value.provider_kind,
            base_url=base_url,
            embedding_model=defaulted_model,
            embedding_provider_kind=defaulted_provider,
            embedding_base_url=defaulted_base_url,
        )
        try:
            with self._connect(writing=True) as connection:
                connection.execute(
                    """
                    INSERT INTO model_profiles (
                        profile_id, name, provider_kind, base_url, model,
                        embedding_model, embedding_provider_kind, embedding_base_url,
                        embedding_dimensions,
                        secret_ref, timeout_seconds, max_concurrency,
                        context_window, disable_thinking, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        profile_id,
                        value.name,
                        value.provider_kind,
                        base_url,
                        value.model,
                        defaulted_model,
                        embedding_provider_kind,
                        embedding_base_url,
                        defaulted_dimensions,
                        secret_ref,
                        value.timeout_seconds,
                        value.max_concurrency,
                        value.context_window,
                        int(value.disable_thinking),
                        now,
                        now,
                    ),
                )
                self._insert_current_version(connection, profile_id, now)
                if value.creation_operation_id is not None:
                    changed = connection.execute(
                        "UPDATE model_profile_creation_operations SET state='committed' "
                        "WHERE operation_id=? AND profile_id=? AND state='pending' "
                        "AND created_revision=1",
                        (value.creation_operation_id, profile_id),
                    )
                    if changed.rowcount != 1:
                        raise ModelCreationOperationLookupError()
        except Exception:
            if secret_ref:
                secrets.delete(secret_ref)
            raise
        return self.require(profile_id, secrets)

    @staticmethod
    def _check_creation_schema(db: sqlite3.Connection) -> None:
        rows = db.execute("PRAGMA table_info(model_profile_creation_operations)").fetchall()
        actual = {row[1]: (row[2].upper(), row[3], row[5]) for row in rows}
        if actual != {
            "operation_id": ("TEXT", 1, 1),
            "profile_id": ("TEXT", 1, 0),
            "state": ("TEXT", 1, 0),
            "created_revision": ("INTEGER", 1, 0),
        }:
            raise ModelCreationOperationLookupError()

    @contextmanager
    def _creation_connection(self, *, writing: bool = False) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        checkout = (
            database.write(create=False, row_factory=sqlite3.Row)
            if writing
            else database.read(row_factory=sqlite3.Row)
        )
        with checkout as db:
            if writing:
                take_the_write_lock_first(db)
            else:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
            yield db

    def _reserve_creation_operation(self, operation_id: str, profile_id: str) -> None:
        _creation_operation_id(operation_id)
        try:
            # Commit before any OS secret write. A pending operation is a fence,
            # not permission to retry or evidence that no write took place.
            with self._creation_connection(writing=True) as db, db:
                # Schema owned by `memory_server.migrations` (migration 30).
                db.execute(profile_creation_operations_ddl("sqlite"))
                self._check_creation_schema(db)
                if db.execute(
                    "SELECT 1 FROM model_profile_creation_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone():
                    raise ModelCreationOperationConflictError()
                db.execute(
                    "INSERT INTO model_profile_creation_operations VALUES (?,?,'pending',1)",
                    (operation_id, profile_id),
                )
        except sqlite3.Error:
            raise ModelCreationOperationLookupError() from None

    def lookup_creation_operation(self, operation_id: str) -> ModelCreationOperationReceipt:
        """Read commit evidence only; pending/not_found never authorize resending a POST."""
        _creation_operation_id(operation_id)
        absent: ModelCreationOperationReceipt = {
            "operation_id": operation_id,
            "state": "not_found",
            "profile_id": None,
            "created_revision": None,
        }
        try:
            with self._creation_connection() as db:
                table = db.execute(
                    "SELECT type FROM sqlite_master WHERE name='model_profile_creation_operations'"
                ).fetchone()
                if table is None:
                    return absent
                if table[0] != "table":
                    raise ModelCreationOperationLookupError()
                self._check_creation_schema(db)
                row = db.execute(
                    "SELECT operation_id,profile_id,state,created_revision "
                    "FROM model_profile_creation_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    return absent
                _creation_operation_id(row["operation_id"])
                _creation_operation_id(row["profile_id"])
                if (
                    row["operation_id"] != operation_id
                    or row["state"] not in {"pending", "committed"}
                    or type(row["created_revision"]) is not int
                    or row["created_revision"] != 1
                ):
                    raise ModelCreationOperationLookupError()
                state: Literal["pending", "committed", "deleted"] = row["state"]
                if (
                    state == "committed"
                    and not db.execute(
                        "SELECT 1 FROM model_profiles WHERE profile_id=?", (row["profile_id"],)
                    ).fetchone()
                ):
                    state = "deleted"
                return {
                    "operation_id": operation_id,
                    "state": state,
                    "profile_id": row["profile_id"],
                    "created_revision": 1,
                }
        except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
            raise ModelCreationOperationLookupError() from None

    def abandon_creation_operation(self, operation_id: str) -> ModelCreationOperationReceipt:
        """Cancel a pending create only while no profile row exists."""
        _creation_operation_id(operation_id)
        try:
            with self._creation_connection(writing=True) as db, db:
                self._check_creation_schema(db)
                row = db.execute(
                    "SELECT profile_id,state,created_revision "
                    "FROM model_profile_creation_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    return {
                        "operation_id": operation_id,
                        "state": "not_found",
                        "profile_id": None,
                        "created_revision": None,
                    }
                profile_id = str(row["profile_id"])
                state = str(row["state"])
                if state == "pending":
                    if db.execute(
                        "SELECT 1 FROM model_profiles WHERE profile_id=?", (profile_id,)
                    ).fetchone():
                        raise ModelCreationOperationLookupError()
                    changed = db.execute(
                        "DELETE FROM model_profile_creation_operations "
                        "WHERE operation_id=? AND profile_id=? AND state='pending'",
                        (operation_id, profile_id),
                    )
                    if changed.rowcount != 1:
                        raise ModelCreationOperationLookupError()
                    return {
                        "operation_id": operation_id,
                        "state": "deleted",
                        "profile_id": profile_id,
                        "created_revision": 1,
                    }
                if state == "committed":
                    terminal_state: Literal["committed", "deleted"] = "committed"
                elif state == "deleted":
                    terminal_state = "deleted"
                else:
                    raise ModelCreationOperationLookupError()
                return {
                    "operation_id": operation_id,
                    "state": terminal_state,
                    "profile_id": profile_id,
                    "created_revision": 1,
                }
        except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
            raise ModelCreationOperationLookupError() from None

    def get(self, profile_id: str, secrets: SecretStore) -> ModelProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return self._to_profile(row, secrets) if row else None

    def require(self, profile_id: str, secrets: SecretStore) -> ModelProfile:
        profile = self.get(profile_id, secrets)
        if profile is None:
            raise KeyError(profile_id)
        return profile

    def list(self, secrets: SecretStore) -> list[ModelProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_profiles ORDER BY created_at, profile_id"
            ).fetchall()
        return [self._to_profile(row, secrets) for row in rows]

    def active(self, secrets: SecretStore) -> ModelProfile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM model_profiles WHERE is_active = 1").fetchone()
        return self._to_profile(row, secrets) if row else None

    def active_identity(self) -> tuple[str, int] | None:
        """Recheck scheduling identity without rereading credentials for every batch."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_id, revision FROM model_profiles WHERE is_active = 1"
            ).fetchone()
        return (str(row["profile_id"]), int(row["revision"])) if row else None

    def update(
        self,
        profile_id: str,
        patch: ModelProfilePatch,
        secrets: SecretStore,
    ) -> ModelProfile:
        current = self.require(profile_id, secrets)
        base_url = patch.base_url if patch.base_url is not None else current.base_url
        if current.provider_kind is ProviderKind.HY_DEFAULT and patch.base_url is not None:
            raise ValueError("HY default endpoint is managed by the product")
        if current.provider_kind is not ProviderKind.HY_DEFAULT and base_url:
            validate_model_base_url(base_url)
        embedding_model = (
            patch.embedding_model if patch.embedding_model is not None else current.embedding_model
        )
        embedding_provider_kind, embedding_base_url = resolve_embedding_endpoint(
            provider_kind=current.provider_kind,
            base_url=base_url,
            embedding_model=embedding_model,
            embedding_provider_kind=(
                patch.embedding_provider_kind
                if patch.embedding_provider_kind is not None
                else current.embedding_provider_kind
            ),
            embedding_base_url=(
                patch.embedding_base_url
                if patch.embedding_base_url is not None
                else current.embedding_base_url
            ),
        )
        if embedding_provider_kind in {ProviderKind.HY_DEFAULT, ProviderKind.ANTHROPIC}:
            raise ValueError("embedding provider must be OpenAI-compatible, Ollama, or local ONNX")
        if (
            embedding_provider_kind is not ProviderKind.LOCAL_ONNX
            and embedding_base_url is not None
        ):
            validate_model_base_url(embedding_base_url)
        validate_embedding_credential_boundary(
            provider_kind=current.provider_kind,
            base_url=base_url,
            embedding_provider_kind=embedding_provider_kind,
            embedding_base_url=embedding_base_url,
        )
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True, lock_first=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM model_profiles
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()
            if row is None:
                raise KeyError(profile_id)
            if row["revision"] != patch.expected_revision or current.revision != row["revision"]:
                raise ModelProfileConflictError("Model profile revision changed")
            next_values = {
                "name": patch.name or current.name,
                "base_url": base_url,
                "model": patch.model or current.model,
                "embedding_model": embedding_model,
                "embedding_provider_kind": embedding_provider_kind,
                "embedding_base_url": embedding_base_url,
                "embedding_dimensions": (
                    patch.embedding_dimensions
                    if patch.embedding_dimensions is not None
                    else current.embedding_dimensions
                ),
                "timeout_seconds": patch.timeout_seconds or current.timeout_seconds,
                "max_concurrency": patch.max_concurrency or current.max_concurrency,
                "context_window": (
                    patch.context_window
                    if "context_window" in patch.model_fields_set
                    else current.context_window
                ),
                "disable_thinking": int(
                    patch.disable_thinking
                    if patch.disable_thinking is not None
                    else current.disable_thinking
                ),
            }
            if (
                patch.api_key is None
                and not patch.clear_api_key
                and all(row[key] == value for key, value in next_values.items())
            ):
                # Probe/activation can change without a profile revision. Return
                # this transaction's metadata without another credential read.
                return current.model_copy(
                    update={
                        "is_active": bool(row["is_active"]),
                        "capabilities": ModelCapabilities(
                            endpoint_reachable=bool(row["endpoint_reachable"]),
                            model_available=bool(row["model_available"]),
                            structured_output=bool(row["structured_output"]),
                            embedding_available=bool(row["embedding_available"]),
                        ),
                        "updated_at": datetime.fromisoformat(row["updated_at"]),
                    }
                )
            # User changes to service/model/credentials need a new explicit activation.
            # Probe-discovered embedding width alone retains its existing behavior.
            deactivate = (
                patch.api_key is not None
                or patch.clear_api_key
                or any(
                    row[key] != next_values[key]
                    for key in (
                        "base_url",
                        "model",
                        "embedding_model",
                        "embedding_provider_kind",
                        "embedding_base_url",
                        "disable_thinking",
                    )
                )
            )
            old_ref = cast(str | None, row["secret_ref"])
            new_ref = old_ref
            created_ref: str | None = None
            try:
                if patch.clear_api_key:
                    new_ref = None
                elif patch.api_key is not None:
                    new_ref = f"model-profile:{profile_id}"
                    secrets.put(new_ref, patch.api_key.get_secret_value())
                    if new_ref != old_ref:
                        created_ref = new_ref
                changed = connection.execute(
                    """
                    UPDATE model_profiles SET
                        name = ?, base_url = ?, model = ?,
                        embedding_model = ?, embedding_provider_kind = ?,
                        embedding_base_url = ?, embedding_dimensions = ?,
                        secret_ref = ?,
                        timeout_seconds = ?, max_concurrency = ?,
                        context_window = ?, disable_thinking = ?,
                        revision = revision + 1,
                        endpoint_reachable = 0, model_available = 0,
                        structured_output = 0, embedding_available = 0,
                        is_active = CASE WHEN ? THEN 0 ELSE is_active END,
                        updated_at = ?
                    WHERE profile_id = ? AND revision = ?
                    """,
                    (
                        next_values["name"],
                        next_values["base_url"],
                        next_values["model"],
                        next_values["embedding_model"],
                        next_values["embedding_provider_kind"],
                        next_values["embedding_base_url"],
                        next_values["embedding_dimensions"],
                        new_ref,
                        next_values["timeout_seconds"],
                        next_values["max_concurrency"],
                        next_values["context_window"],
                        next_values["disable_thinking"],
                        int(deactivate),
                        now,
                        profile_id,
                        patch.expected_revision,
                    ),
                )
                if changed.rowcount != 1:
                    raise ModelProfileConflictError("Model profile revision changed")
                if patch.api_key is not None and old_ref and old_ref != new_ref:
                    connection.execute(
                        "UPDATE model_profile_versions SET secret_ref=? "
                        "WHERE profile_id=? AND secret_ref=?",
                        (new_ref, profile_id, old_ref),
                    )
                self._insert_current_version(connection, profile_id, now)
            except Exception:
                if created_ref:
                    secrets.delete(created_ref)
                raise
        if patch.clear_api_key:
            self._purge_profile_secrets(profile_id, secrets)
        elif patch.api_key is not None and old_ref and old_ref != new_ref:
            secrets.delete(old_ref)
        return self.require(profile_id, secrets)

    def _purge_profile_secrets(
        self,
        profile_id: str,
        secrets: SecretStore,
    ) -> None:
        with self._connect(writing=True) as connection:
            rows = connection.execute(
                """
                SELECT secret_ref FROM model_profile_versions
                WHERE profile_id = ? AND secret_ref IS NOT NULL
                """,
                (profile_id,),
            ).fetchall()
            connection.execute(
                """
                UPDATE model_profile_versions SET secret_ref = NULL
                WHERE profile_id = ?
                """,
                (profile_id,),
            )
        for row in rows:
            secrets.delete(cast(str, row["secret_ref"]))

    @staticmethod
    def activate_in_connection(
        connection: sqlite3.Connection,
        profile: ModelProfileSnapshot,
        *,
        expected_secret_ref: str | None,
        switch_active: bool = True,
    ) -> None:
        """Switch exact, already-probed identity in caller transaction; never schedules."""
        if not connection.in_transaction:
            raise ModelProfileConflictError("caller_transaction_required")
        previous_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        name = "activation_" + uuid4().hex
        connection.execute(f"SAVEPOINT {name}")
        try:
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE profile_id=?", (profile.profile_id,)
            ).fetchone()
            version = connection.execute(
                "SELECT secret_ref FROM model_profile_versions WHERE profile_id=? AND revision=?",
                (profile.profile_id, profile.revision),
            ).fetchone()
            if (
                row is None
                or row["revision"] != profile.revision
                or version is None
                or row["secret_ref"] != expected_secret_ref
                or version["secret_ref"] != expected_secret_ref
            ):
                raise ModelProfileConflictError("model_profile_changed")
            if profile.provider_kind not in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.OLLAMA}:
                raise ModelProfileUnavailableError("model_provider_not_supported")
            if profile.provider_kind is ProviderKind.OPENAI_COMPATIBLE and not expected_secret_ref:
                raise ModelProfileUnavailableError("model_secret_required")
            if not all(
                row[key] for key in ("endpoint_reachable", "model_available", "embedding_available")
            ):
                raise ModelProfileUnavailableError("model_profile_probe_required")
            if not profile.embedding_model:
                raise ModelProfileUnavailableError("embedding_model_required")
            # Compare complete stored and versioned identity before changing any active flag.
            for table in ("model_profiles", "model_profile_versions"):
                identity = connection.execute(
                    f"SELECT {_PROFILE_SNAPSHOT_COLUMNS} FROM {table} "
                    "WHERE profile_id=? AND revision=?",
                    (profile.profile_id, profile.revision),
                ).fetchone()
                if (
                    identity is None
                    or ModelProfileSnapshot.model_validate(dict(identity)) != profile
                ):
                    raise ModelProfileConflictError("model_profile_changed")
            if switch_active:
                connection.execute("UPDATE model_profiles SET is_active=0 WHERE is_active=1")
                connection.execute(
                    "UPDATE model_profiles SET is_active=1 WHERE profile_id=? AND revision=?",
                    (profile.profile_id, profile.revision),
                )
            active = connection.execute(
                "SELECT profile_id,revision FROM model_profiles WHERE is_active=1"
            ).fetchone()
            if active is None or tuple(active) != (profile.profile_id, profile.revision):
                raise ModelProfileConflictError("model_profile_changed")
        except BaseException:
            connection.execute(f"ROLLBACK TO {name}")
            connection.execute(f"RELEASE {name}")
            raise
        else:
            connection.execute(f"RELEASE {name}")
        finally:
            connection.row_factory = previous_factory

    def activate(self, profile_id: str, secrets: SecretStore) -> ModelProfile:
        profile = self.require(profile_id, secrets)
        if profile.provider_kind in {ProviderKind.HY_DEFAULT, ProviderKind.ANTHROPIC}:
            raise ModelProfileUnavailableError("model_provider_not_supported")
        if not profile.embedding_model:
            raise ModelProfileUnavailableError("embedding_model_required")
        if not (
            profile.capabilities.endpoint_reachable
            and profile.capabilities.model_available
            # structured_output is deliberately not required. It is a gateway
            # capability, not proof that the model cannot generate. HY does
            # the extracting without this constraint; recall and the graph
            # still require the embedding capability below.
            and profile.capabilities.embedding_available
        ):
            raise ModelProfileUnavailableError("model_profile_probe_required")
        with self._connect(writing=True) as connection:
            connection.execute("UPDATE model_profiles SET is_active = 0")
            connection.execute(
                "UPDATE model_profiles SET is_active = 1 WHERE profile_id = ?",
                (profile_id,),
            )
        return self.require(profile_id, secrets)

    def delete(self, profile_id: str, secrets: SecretStore) -> None:
        with self._connect(writing=True) as connection:
            row = connection.execute(
                "SELECT profile_id FROM model_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise KeyError(profile_id)
            secret_refs = {
                item["secret_ref"]
                for item in connection.execute(
                    """
                    SELECT secret_ref FROM model_profile_versions
                    WHERE profile_id = ? AND secret_ref IS NOT NULL
                    """,
                    (profile_id,),
                )
            }
            connection.execute(
                "DELETE FROM model_profiles WHERE profile_id = ?",
                (profile_id,),
            )
        for secret_ref in secret_refs:
            secrets.delete(secret_ref)

    def record_capabilities(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        capabilities: ModelCapabilities,
        secrets: SecretStore,
    ) -> ModelProfile:
        with self._connect(writing=True) as connection:
            changed = connection.execute(
                """
                UPDATE model_profiles SET endpoint_reachable = ?,
                    model_available = ?, structured_output = ?,
                    embedding_available = ?, updated_at = ?
                WHERE profile_id = ? AND revision = ?
                """,
                (
                    capabilities.endpoint_reachable,
                    capabilities.model_available,
                    capabilities.structured_output,
                    capabilities.embedding_available,
                    datetime.now(UTC).isoformat(),
                    profile_id,
                    expected_revision,
                ),
            )
            if changed.rowcount != 1:
                raise ModelProfileConflictError("Model profile changed while probing")
        return self.require(profile_id, secrets)

    def secret_ref(self, profile_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT secret_ref FROM model_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return cast(str | None, row["secret_ref"])

    def cleanup_unreferenced_secrets(self, secrets: SecretStore) -> int:
        """Remove legacy revision credentials no current profile references."""
        with self._connect() as connection:
            current = {
                str(row[0])
                for row in connection.execute(
                    "SELECT secret_ref FROM model_profiles WHERE secret_ref IS NOT NULL"
                )
            }
            stale = tuple(
                sorted(
                    {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT secret_ref FROM model_profile_versions "
                            "WHERE secret_ref IS NOT NULL"
                        )
                    }
                    - current
                )
            )
        if not stale:
            return 0
        delete_many = getattr(secrets, "delete_many", None)
        if delete_many is not None:
            delete_many(stale)
        else:
            for secret_ref in stale:
                secrets.delete(secret_ref)
        with self._connect(writing=True) as connection:
            placeholders = ",".join("?" for _ in stale)
            connection.execute(
                f"UPDATE model_profile_versions SET secret_ref=NULL "
                f"WHERE secret_ref IN ({placeholders})",
                stale,
            )
        return len(stale)

    def snapshot(
        self,
        profile_id: str,
        revision: int,
    ) -> ModelProfileSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM model_profile_versions
                WHERE profile_id = ? AND revision = ?
                """,
                (profile_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError((profile_id, revision))
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

    def resolve_snapshot_secret(
        self,
        profile_id: str,
        revision: int,
        secrets: SecretStore,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT secret_ref FROM model_profile_versions
                WHERE profile_id = ? AND revision = ?
                """,
                (profile_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError((profile_id, revision))
        secret_ref = cast(str | None, row["secret_ref"])
        return secrets.resolve(secret_ref) if secret_ref else None

    @contextmanager
    def _connect(
        self, *, writing: bool = False, lock_first: bool = True
    ) -> Iterator[sqlite3.Connection]:
        database = main_database(self.database_path)
        checkout = (
            database.write(foreign_keys=True, row_factory=sqlite3.Row)
            if writing
            else database.read(foreign_keys=True, row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            # Waiting is not enough for a method that checks before it writes, and
            # the ones here do: which profile is current, what version it is on.
            # Read first and SQLite refuses the write outright rather than waiting.
            if writing and lock_first:
                take_the_write_lock_first(connection)
            yield connection

    @staticmethod
    def _insert_current_version(
        connection: sqlite3.Connection,
        profile_id: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_profile_versions (
                profile_id, revision, provider_kind, base_url, model,
                embedding_model, embedding_provider_kind, embedding_base_url,
                embedding_dimensions,
                timeout_seconds, max_concurrency, context_window,
                disable_thinking,
                secret_ref, created_at
            )
            SELECT profile_id, revision, provider_kind, base_url, model,
                   embedding_model, embedding_provider_kind, embedding_base_url,
                   embedding_dimensions,
                   timeout_seconds, max_concurrency, context_window,
                   disable_thinking,
                   secret_ref, ?
            FROM model_profiles WHERE profile_id = ?
            """,
            (created_at, profile_id),
        )

    @staticmethod
    def _to_profile(row: sqlite3.Row, secrets: SecretStore) -> ModelProfile:
        secret_ref = row["secret_ref"]
        credential_state: Literal["available", "missing", "unavailable"]
        try:
            has_key = bool(secret_ref and secrets.has(secret_ref))
            credential_state = "available" if has_key else "missing"
        except SecretStoreUnavailableError:
            has_key, credential_state = False, "unavailable"
        return ModelProfile(
            profile_id=row["profile_id"],
            name=row["name"],
            provider_kind=row["provider_kind"],
            base_url=row["base_url"],
            model=row["model"],
            embedding_model=row["embedding_model"],
            embedding_provider_kind=row["embedding_provider_kind"],
            embedding_base_url=row["embedding_base_url"],
            embedding_dimensions=row["embedding_dimensions"],
            has_api_key=has_key,
            has_configured_api_key=secret_ref is not None,
            credential_state=credential_state,
            timeout_seconds=row["timeout_seconds"],
            max_concurrency=row["max_concurrency"],
            context_window=row["context_window"],
            disable_thinking=bool(row["disable_thinking"]),
            capabilities=ModelCapabilities(
                endpoint_reachable=bool(row["endpoint_reachable"]),
                model_available=bool(row["model_available"]),
                structured_output=bool(row["structured_output"]),
                embedding_available=bool(row["embedding_available"]),
            ),
            revision=row["revision"],
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def disable_thinking_params(
    profile: ModelProfile | ModelProfileSnapshot,
) -> dict[str, object]:
    """Provider-specific thinking-off params, empty unless the profile opts in.

    The one mapping for every request Core builds itself and for the SDK's
    `llm.extra_body`, so a model that thinks on one path thinks on none.
    """
    if not profile.disable_thinking:
        return {}
    if profile.provider_kind is ProviderKind.OPENAI_COMPATIBLE:
        if urlparse(profile.base_url or "").hostname == "api.deepseek.com":
            return {"thinking": {"type": "disabled"}}
        return {"reasoning_effort": "none"}
    if profile.provider_kind is ProviderKind.OLLAMA:
        return {"think": False}
    return {}


def attach_disable_thinking(
    profile: ModelProfile | ModelProfileSnapshot,
    payload: dict[str, object],
) -> dict[str, object]:
    """Attach provider-specific thinking-off params when the profile opts in."""
    payload.update(disable_thinking_params(profile))
    return payload


def resolve_embedding_endpoint(
    *,
    provider_kind: ProviderKind,
    base_url: str | None,
    embedding_model: str | None,
    embedding_provider_kind: ProviderKind | None,
    embedding_base_url: str | None,
) -> tuple[ProviderKind | None, str | None]:
    if embedding_model is None:
        return None, None
    if embedding_provider_kind is ProviderKind.LOCAL_ONNX:
        return ProviderKind.LOCAL_ONNX, None
    if embedding_provider_kind is None:
        return provider_kind, embedding_base_url or base_url
    return (
        embedding_provider_kind,
        embedding_base_url or DEFAULT_BASE_URLS.get(embedding_provider_kind),
    )


def validate_model_base_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Model base URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Model base URL cannot contain credentials")
    if not parsed.hostname:
        raise ValueError("Model base URL must contain a host")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError("Remote model endpoints must use HTTPS")


def validate_embedding_credential_boundary(
    *,
    provider_kind: ProviderKind,
    base_url: str | None,
    embedding_provider_kind: ProviderKind | None,
    embedding_base_url: str | None,
) -> None:
    # Local ONNX never leaves the machine, so the shared-origin rule that
    # protects a remote embedding key does not apply. Ollama is the same
    # class of exception: it is local and uses no shared remote credential.
    if embedding_provider_kind in {
        None,
        ProviderKind.OLLAMA,
        ProviderKind.LOCAL_ONNX,
    }:
        return
    if (
        provider_kind is not ProviderKind.OPENAI_COMPATIBLE
        or embedding_provider_kind is not ProviderKind.OPENAI_COMPATIBLE
        or _url_origin(base_url) != _url_origin(embedding_base_url)
    ):
        raise ValueError("Remote embedding must share the generation endpoint origin and API key")


def apply_builtin_embedding_defaults(
    *,
    embedding_model: str | None,
    embedding_provider_kind: ProviderKind | None,
    embedding_base_url: str | None,
    embedding_dimensions: int | None,
) -> tuple[ProviderKind | None, str | None, str | None, int | None]:
    """Fill in the built-in model when the caller left embedding unset.

    An explicit embedding configuration is left alone. The point of the
    built-in is that a new profile needs no embedding fields at all.
    """
    from one_memory.embedding_assets import (
        BUILTIN_EMBEDDING_DIMENSIONS,
        BUILTIN_EMBEDDING_MODEL,
    )

    if embedding_model is not None or embedding_provider_kind is not None:
        return (
            embedding_provider_kind,
            embedding_model,
            embedding_base_url,
            embedding_dimensions,
        )
    return (
        ProviderKind.LOCAL_ONNX,
        BUILTIN_EMBEDDING_MODEL,
        None,
        embedding_dimensions or BUILTIN_EMBEDDING_DIMENSIONS,
    )


def _url_origin(value: str | None) -> tuple[str, str, int | None] | None:
    if value is None:
        return None
    parsed = urlparse(value)
    scheme = parsed.scheme.casefold()
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return (scheme, (parsed.hostname or "").casefold(), parsed.port or default_port)


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
