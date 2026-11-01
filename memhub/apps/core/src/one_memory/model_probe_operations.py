"""One durable reservation per probe; never replay a possibly dispatched operation."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database
from one_memory.embedding_spaces import probe_width_allowed
from one_memory.memory_server.migrations import model_probe_operations_ddl
from one_memory.model_probe import ModelProbeResult, ProbeStage
from one_memory.model_profiles import ModelCapabilities, ModelProfileStore


class ProbeOperationConflict(RuntimeError):
    pass


class ProbeOperationUnavailable(RuntimeError):
    pass


def canonical_id(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_probe_operation_id") from None
    return value


class ModelProbeOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=36, max_length=36)
    expected_profile_revision: int = Field(ge=1)

    @field_validator("operation_id")
    @classmethod
    def identifier(cls, value: str) -> str:
        return canonical_id(value)


class ModelProbeOperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    operation_id: str
    profile_id: str | None
    expected_profile_revision: int | None
    state: Literal["pending", "completed", "failed", "not_found"]
    result: ModelProbeResult | None = None
    code: Literal["model_probe_profile_changed", "model_probe_execution_failed"] | None = None
    stale: bool | None = None


#: Schema owned by `memory_server.migrations` (migration 18). No local copy.
SCHEMA = model_probe_operations_ddl("sqlite")

#: A reservation fences new probes so a possibly dispatched test is never
#: replayed. That fence must not be forever: when the dispatch outcome was
#: lost (crash, storage failure, transport meltdown) the profile would stay
#: fenced and the onboarding form locked with no way back. After this long a
#: pending reservation reads back as an explicit execution failure instead.
_PENDING_TTL = timedelta(minutes=10)


def _reservation_stamp() -> str:
    return datetime.now(UTC).isoformat()


def _reservation_floor() -> str:
    return (datetime.now(UTC) - _PENDING_TTL).isoformat()


def stamp(row: sqlite3.Row) -> str:
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()


class ModelProbeOperations:
    def __init__(self, store: ModelProfileStore):
        self.store = store

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            database = main_database(self.store.database_path)
            with (
                database.write(create=False, row_factory=sqlite3.Row)
                if write
                else database.read(row_factory=sqlite3.Row)
            ) as db:
                if write:
                    take_the_write_lock_first(db)
                else:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("BEGIN")
                with db:
                    yield db
        except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
            raise ProbeOperationUnavailable("model_probe_operation_unavailable") from None

    def lookup(self, operation_id: str) -> ModelProbeOperationReceipt:
        canonical_id(operation_id)
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_probe_operations'"
            ).fetchone():
                row = None
            else:
                self._validate_schema(db)
                row = db.execute(
                    "SELECT * FROM model_probe_operations WHERE operation_id=?", (operation_id,)
                ).fetchone()
            if row is None:
                return ModelProbeOperationReceipt(
                    operation_id=operation_id,
                    profile_id=None,
                    expected_profile_revision=None,
                    state="not_found",
                )
            return self._receipt(db, row)

    @staticmethod
    def _validate_schema(db: sqlite3.Connection) -> None:
        columns = {
            row[1]: (row[2].upper(), row[3], row[5])
            for row in db.execute("PRAGMA table_info(model_probe_operations)")
        }
        if columns != {
            "operation_id": ("TEXT", 1, 1),
            "profile_id": ("TEXT", 1, 0),
            "input_revision": ("INTEGER", 1, 0),
            "input_stamp": ("TEXT", 1, 0),
            "state": ("TEXT", 1, 0),
            "result_json": ("TEXT", 0, 0),
            "result_stamp": ("TEXT", 0, 0),
            "code": ("TEXT", 0, 0),
            "reserved_at": ("TEXT", 0, 0),
        }:
            raise ValueError("invalid_probe_operation_schema")

    @staticmethod
    def _reservation_expired(row: sqlite3.Row) -> bool:
        """True when a pending reservation has fenced long enough.

        `reserved_at IS NULL` means the row predates the column: those are
        exactly the reservations that could already be stuck, so they expire
        immediately. A malformed stamp is treated the same way.
        """
        if row["state"] != "pending":
            return False
        reserved_at = row["reserved_at"]
        if not isinstance(reserved_at, str) or not reserved_at:
            return True
        try:
            reserved = datetime.fromisoformat(reserved_at)
        except ValueError:
            return True
        return datetime.now(UTC) - reserved > _PENDING_TTL

    def _receipt(self, db: sqlite3.Connection, row: sqlite3.Row) -> ModelProbeOperationReceipt:
        canonical_id(row["operation_id"])
        canonical_id(row["profile_id"])
        if (
            row["state"] not in {"pending", "completed", "failed"}
            or type(row["input_revision"]) is not int
            or row["input_revision"] < 1
            or not isinstance(row["profile_id"], str)
            or not row["profile_id"]
            or not self._digest(row["input_stamp"])
        ):
            raise ValueError("invalid_probe_receipt")
        if self._reservation_expired(row):
            # The dispatch outcome was lost; reporting a failed receipt ends
            # the fence and lets the caller re-test with a new operation.
            return ModelProbeOperationReceipt(
                operation_id=row["operation_id"],
                profile_id=row["profile_id"],
                expected_profile_revision=row["input_revision"],
                state="failed",
                code="model_probe_execution_failed",
                stale=True,
            )
        if row["result_json"] is not None and (
            not isinstance(row["result_json"], str) or len(row["result_json"]) > 16_384
        ):
            raise ValueError("invalid_probe_result")
        result = (
            ModelProbeResult.model_validate_json(row["result_json"], strict=True)
            if row["result_json"]
            else None
        )
        if row["state"] == "completed":
            if (
                result is None
                or result.profile_id != row["profile_id"]
                or result.input_profile_revision != row["input_revision"]
                or result.profile_revision not in {row["input_revision"], row["input_revision"] + 1}
                or row["code"] is not None
                or not self._digest(row["result_stamp"])
            ):
                raise ValueError("invalid_probe_result")
            if not 1 <= len(result.stages) <= 4 or any(
                stage.stage
                not in {
                    "endpoint",
                    "provider",
                    "authentication",
                    "model",
                    "structured_output",
                    "embedding",
                }
                or stage.latency_ms < 0
                for stage in result.stages
            ):
                raise ValueError("invalid_probe_stages")
        elif result is not None or row["result_stamp"] is not None:
            raise ValueError("unexpected_probe_result")
        if row["state"] == "pending" and row["code"] is not None:
            raise ValueError("invalid_probe_pending")
        if row["state"] == "failed" and row["code"] not in {
            "model_probe_profile_changed",
            "model_probe_execution_failed",
        }:
            raise ValueError("invalid_probe_failed")
        current = db.execute(
            "SELECT * FROM model_profiles WHERE profile_id=?", (row["profile_id"],)
        ).fetchone()
        expected = row["result_stamp"] if result is not None else row["input_stamp"]
        return ModelProbeOperationReceipt(
            operation_id=row["operation_id"],
            profile_id=row["profile_id"],
            expected_profile_revision=row["input_revision"],
            state=row["state"],
            result=result,
            code=row["code"],
            stale=current is None or stamp(current) != expected,
        )

    @staticmethod
    def _digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        )

    def reserve(
        self, profile_id: str, request: ModelProbeOperationRequest
    ) -> ModelProbeOperationReceipt | None:
        with self._connect(write=True) as db:
            db.execute(SCHEMA)
            self._validate_schema(db)
            previous = db.execute(
                "SELECT * FROM model_probe_operations WHERE operation_id=?", (request.operation_id,)
            ).fetchone()
            if previous is not None:
                if (
                    previous["profile_id"] != profile_id
                    or previous["input_revision"] != request.expected_profile_revision
                ):
                    raise ProbeOperationConflict("model_probe_operation_conflict")
                receipt = self._receipt(db, previous)
                if receipt.state == "pending":
                    raise ProbeOperationConflict("model_probe_operation_pending")
                return receipt
            row = db.execute(
                "SELECT * FROM model_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if row is None or row["revision"] != request.expected_profile_revision:
                raise ProbeOperationConflict("model_probe_profile_changed")
            if db.execute(
                "SELECT 1 FROM model_probe_operations WHERE profile_id=? AND state='pending'"
                " AND reserved_at IS NOT NULL AND reserved_at >= ?",
                (profile_id, _reservation_floor()),
            ).fetchone():
                raise ProbeOperationConflict("model_probe_operation_pending")
            db.execute(
                "INSERT INTO model_probe_operations"
                " (operation_id, profile_id, input_revision, input_stamp, state,"
                "  result_json, result_stamp, code, reserved_at)"
                " VALUES (?,?,?,?,'pending',NULL,NULL,NULL,?)",
                (
                    request.operation_id,
                    profile_id,
                    request.expected_profile_revision,
                    stamp(row),
                    _reservation_stamp(),
                ),
            )
        return None

    def check(self, operation_id: str) -> None:
        with self._connect() as db:
            self._current(db, operation_id)

    def assert_no_pending(self, profile_id: str) -> None:
        """Legacy calls cannot bypass an unresolved identified probe."""
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_probe_operations'"
            ).fetchone():
                return
            self._validate_schema(db)
            if db.execute(
                "SELECT 1 FROM model_probe_operations WHERE profile_id=? AND state='pending'"
                " AND reserved_at IS NOT NULL AND reserved_at >= ?",
                (profile_id, _reservation_floor()),
            ).fetchone():
                raise ProbeOperationConflict("model_probe_operation_pending")

    def _current(
        self, db: sqlite3.Connection, operation_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        self._validate_schema(db)
        operation = db.execute(
            "SELECT * FROM model_probe_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if operation is None or operation["state"] != "pending":
            raise ProbeOperationConflict("model_probe_operation_conflict")
        profile = db.execute(
            "SELECT * FROM model_profiles WHERE profile_id=?", (operation["profile_id"],)
        ).fetchone()
        if (
            profile is None
            or profile["revision"] != operation["input_revision"]
            or stamp(profile) != operation["input_stamp"]
        ):
            raise ProbeOperationConflict("model_probe_profile_changed")
        return operation, profile

    def finish(
        self, operation_id: str, stages: tuple[ProbeStage, ...], capabilities: ModelCapabilities
    ) -> ModelProbeResult:
        with self._connect(write=True) as db:
            operation, profile = self._current(db, operation_id)
            width = next(
                (
                    s.embedding_dimensions
                    for s in stages
                    if s.stage == "embedding" and s.embedding_dimensions
                ),
                None,
            )
            revision = profile["revision"]
            now = datetime.now(UTC).isoformat()
            # The profile that fills the live embedding space records only the
            # space's own width: another one would move writes to new, empty
            # collections (B2). Using it takes an embedding switch.
            if (
                width is not None
                and width != profile["embedding_dimensions"]
                and probe_width_allowed(
                    db,
                    profile["profile_id"],
                    width,
                    data_dir=self.store.database_path.parent,
                )
            ):
                revision += 1
                db.execute(
                    "UPDATE model_profiles SET embedding_dimensions=?,revision=? "
                    "WHERE profile_id=?",
                    (width, revision, profile["profile_id"]),
                )
                self.store._insert_current_version(db, profile["profile_id"], now)
            db.execute(
                "UPDATE model_profiles SET endpoint_reachable=?,model_available=?,"
                "structured_output=?,embedding_available=?,updated_at=? WHERE profile_id=?",
                (
                    capabilities.endpoint_reachable,
                    capabilities.model_available,
                    capabilities.structured_output,
                    capabilities.embedding_available,
                    now,
                    profile["profile_id"],
                ),
            )
            result = ModelProbeResult(
                profile_id=profile["profile_id"],
                profile_revision=revision,
                input_profile_revision=operation["input_revision"],
                stages=stages,
                capabilities=capabilities,
            )
            final = db.execute(
                "SELECT * FROM model_profiles WHERE profile_id=?", (profile["profile_id"],)
            ).fetchone()
            db.execute(
                "UPDATE model_probe_operations SET state='completed',result_json=?,result_stamp=? "
                "WHERE operation_id=?",
                (result.model_dump_json(), stamp(final), operation_id),
            )
            return result

    def fail(self, operation_id: str, code: str) -> None:
        if code not in {"model_probe_profile_changed", "model_probe_execution_failed"}:
            raise ValueError("invalid_probe_failure_code")
        with self._connect(write=True) as db:
            self._validate_schema(db)
            db.execute(
                "UPDATE model_probe_operations SET state='failed',code=? "
                "WHERE operation_id=? AND state='pending'",
                (code, operation_id),
            )
