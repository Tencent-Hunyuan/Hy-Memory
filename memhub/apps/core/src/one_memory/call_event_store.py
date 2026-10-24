"""Local-owner call metadata in the existing Core database, never a recorder.

Initialization and retention policy are explicit. No API, authorization grant,
content lookup, or production enablement lives here. Callers must restrict this
store to the local owner; a host identity is attribution, not read authority.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from one_memory.context_feedback import ExplicitRetrievalFeedback, FeedbackReference
from one_memory.database import take_the_write_lock_first
from one_memory.database_access import main_database

HostId = Literal[
    "cursor",
    "codex",
    "claude-code",
    "workbuddy",
    "opencode",
    "qwen-code",
    "hermes",
    "pi",
    "deepseek-harness",
    "zcode",
]
CallTool = Literal["om_recall", "om_conversations", "om_ask", "om_matters"]
CallState = Literal[
    "grounded",
    "empty",
    "partial",
    "timed_out",
    "unavailable",
    "denied",
]
CallDelivery = Literal["unknown", "delivered", "not_delivered"]
HostSessionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$")]
MAX_PAYLOAD_BYTES = 128 * 1024

# SQLite checks byte length before returning the TEXT cell to Python. A corrupt
# oversized cell is an error marker, never a truncated document or an empty row.
_EVENT_COLUMNS = (
    "event_id, occurred_at, host_id, "
    "CASE WHEN typeof(payload) = 'text' "
    f"AND length(CAST(payload AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
    "THEN payload ELSE NULL END AS payload"
)


class _StrictMetadata(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class CallEventReference(_StrictMetadata):
    kind: Literal["raw", "memory"]
    id: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=1)
    source_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")] | None

    @field_validator("id")
    @classmethod
    def identifier_without_controls(cls, value: str) -> str:
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
            raise ValueError("invalid_call_reference_id")
        return value


CallReference = CallEventReference


class CallEvent(_StrictMetadata):
    event_id: UUID
    occurred_at: AwareDatetime
    host_id: HostId | None
    installation_id: UUID | None
    tool: CallTool
    state: CallState
    stage: Literal["core_result", "feedback"]
    delivery: CallDelivery
    adoption: Literal["unknown"]
    host_session_id: HostSessionId | None
    injection_elapsed_ms: float | None = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    raw_count: int = Field(ge=0, le=20)
    memory_count: int = Field(ge=0, le=20)
    references: tuple[CallEventReference, ...] = Field(max_length=128)
    feedback: ExplicitRetrievalFeedback | None = None

    @field_validator("occurred_at")
    @classmethod
    def canonical_time(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(UTC)
        except (ValueError, OverflowError):
            raise ValueError("invalid_call_event_timestamp") from None

    @model_validator(mode="after")
    def result_counts(self) -> Self:
        if self.raw_count + self.memory_count > 20:
            raise ValueError("call_event_result_limit_exceeded")
        # Raw counts measure conversations, not messages; deduplication can also
        # reduce references. Only a reference to an absent result kind is invalid.
        counts = {"raw": self.raw_count, "memory": self.memory_count}
        if any(counts[reference.kind] == 0 for reference in self.references):
            raise ValueError("call_event_reference_without_result")
        if (self.stage == "feedback") != (self.feedback is not None):
            raise ValueError("call_event_feedback_stage_mismatch")
        return self


class CallEventPage(_StrictMetadata):
    total: int = Field(ge=0)
    items: tuple[CallEvent, ...] = Field(max_length=100)


class _Retention(_StrictMetadata):
    retention_days: int = Field(ge=1, le=365)
    max_events: int = Field(ge=1, le=100000)


class _PageQuery(_StrictMetadata):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100000)
    host_id: HostId | None


class CallEventStoreUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("call_event_store_unavailable")


class CallEventConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("call_event_conflict")


_FEEDBACK_NAMESPACE = UUID("6f6c6f52-2f77-4b1e-9d6e-6d656d6f7279")


def _returned_reference(reference: FeedbackReference, source: CallEvent) -> CallEventReference:
    for returned in source.references:
        if (
            returned.kind == reference.kind
            and returned.id == reference.reference_id
            and returned.revision == reference.revision
        ):
            return returned
    raise ValueError("call_event_feedback_reference_not_returned")


def _feedback_event(source: CallEvent, value: ExplicitRetrievalFeedback) -> CallEvent:
    references = tuple(_returned_reference(reference, source) for reference in value.selected_refs)
    identity = json.dumps(
        {
            "consent_version": value.consent_version,
            "feedback_kind": value.feedback_kind,
            "search_event_id": value.search_event_id,
            "selected_refs": [
                {
                    "kind": item.kind,
                    "reference_id": item.reference_id,
                    "revision": item.revision,
                }
                for item in value.selected_refs
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CallEvent.model_validate(
        {
            "event_id": uuid5(_FEEDBACK_NAMESPACE, identity),
            "occurred_at": value.created_at,
            "host_id": None,
            "installation_id": None,
            "tool": source.tool,
            "state": source.state,
            "stage": "feedback",
            "delivery": "unknown",
            "adoption": "unknown",
            "host_session_id": None,
            "injection_elapsed_ms": None,
            "elapsed_ms": 0,
            "raw_count": sum(item.kind == "raw" for item in references),
            "memory_count": sum(item.kind == "memory" for item in references),
            "references": references,
            "feedback": value,
        }
    )


_UUID = TypeAdapter(UUID, config=ConfigDict(strict=True))
_CLEAR_IDS: TypeAdapter[list[UUID]] = TypeAdapter(
    Annotated[list[UUID], Field(max_length=100)], config=ConfigDict(strict=True)
)

# These objects belong only to this module. No foreign keys or triggers can
# cascade a retention/clear operation into Evidence or governance storage.
#
# A1-04 exception, recorded deliberately: this module compares
# `sqlite_master.sql` against these strings to detect a tampered or legacy
# schema, and raises `CallEventStoreUnavailable` on any difference. The text is
# therefore part of the integrity check, not just a create statement — routing
# it through the shared migration and reformatting it would make every existing
# database look corrupt. The canonical DDL also lives in migration 29 for new
# databases; the two must be kept in step, which `test_call_event_ddl_matches_
# the_migration` enforces.
_SCHEMA = {
    "call_events": """CREATE TABLE call_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    occurred_at TEXT NOT NULL,
    host_id TEXT,
    payload TEXT NOT NULL
)""",
    "call_events_time": (
        "CREATE INDEX call_events_time ON call_events(occurred_at DESC, event_id DESC)"
    ),
    "call_events_host_time": (
        "CREATE INDEX call_events_host_time "
        "ON call_events(host_id, occurred_at DESC, event_id DESC)"
    ),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _ready(db: sqlite3.Connection) -> None:
    for name, sql in _SCHEMA.items():
        row = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row is None or row[0] != sql:
            raise CallEventStoreUnavailable()
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='call_events' LIMIT 1"
    ).fetchone():
        raise CallEventStoreUnavailable()


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("call_event_duplicate_json_key")
        result[key] = value
    return result


def _event(row: sqlite3.Row) -> CallEvent:
    payload = row["payload"]
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise CallEventStoreUnavailable()
    try:
        # Pydantic's JSON parser accepts duplicate keys. Check the bounded input
        # first, including nested objects, then retain strict JSON UUID/time parsing.
        decoded = json.loads(payload, object_pairs_hook=_unique_keys)
        if isinstance(decoded, dict):
            delivery_fields = {"delivery", "host_session_id", "injection_elapsed_ms"}
            present = delivery_fields.intersection(decoded)
            if present and present != delivery_fields:
                raise ValueError("partial_call_event_delivery_metadata")
            if not present:
                decoded.update(
                    delivery="unknown",
                    host_session_id=None,
                    injection_elapsed_ms=None,
                )
        value = CallEvent.model_validate_json(json.dumps(decoded))
    except (ValueError, RecursionError):
        raise CallEventStoreUnavailable() from None
    if (
        str(value.event_id) != row["event_id"]
        or _timestamp(value.occurred_at) != row["occurred_at"]
        or value.host_id != row["host_id"]
    ):
        raise CallEventStoreUnavailable()
    return value


class CallEventStore:
    def __init__(self, database_path: Path, *, retention_days: int, max_events: int) -> None:
        self._retention = _Retention(retention_days=retention_days, max_events=max_events)
        try:
            self._database_path = database_path.resolve()
            if not self._database_path.is_file():
                raise CallEventStoreUnavailable()
        except OSError:
            raise CallEventStoreUnavailable() from None

    @contextmanager
    def _connection(
        self, *, writing: bool = False, busy_timeout_ms: int = 5_000
    ) -> Iterator[sqlite3.Connection]:
        database = main_database(self._database_path)
        try:
            with (
                database.write(
                    create=False,
                    row_factory=sqlite3.Row,
                    busy_timeout_ms=busy_timeout_ms,
                )
                if writing
                else database.read(row_factory=sqlite3.Row)
            ) as db:
                if writing:
                    take_the_write_lock_first(db)
                else:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("BEGIN")
                with db:
                    yield db
        except (sqlite3.Error, OSError, ValidationError):
            raise CallEventStoreUnavailable() from None

    def initialize(self) -> None:
        """Explicit atomic schema creation; incompatible existing objects are errors."""
        with self._connection(writing=True) as db:
            for name, sql in _SCHEMA.items():
                existing = db.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()
                if existing is None:
                    db.execute(sql)
                elif existing[0] != sql:
                    raise CallEventStoreUnavailable()
            _ready(db)

    def _cutoff(self) -> str:
        return _timestamp(_now() - timedelta(days=self._retention.retention_days))

    def _prune(self, db: sqlite3.Connection, cutoff: str) -> None:
        db.execute("DELETE FROM call_events WHERE occurred_at < ?", (cutoff,))
        db.execute(
            "DELETE FROM call_events WHERE event_id IN ("
            "SELECT event_id FROM call_events ORDER BY occurred_at DESC, event_id DESC "
            "LIMIT -1 OFFSET ?)",
            (self._retention.max_events,),
        )

    def record(self, event: CallEvent, *, busy_timeout_ms: int = 5_000) -> bool:
        """Insert metadata and prune atomically; False means an identical retained ID.

        Idempotency lasts only while an event is retained. A successful insertion
        can itself be pruned if its timestamp is outside the age/count window.
        An existing conflicting ID fails before any retention changes are made.
        """
        value = CallEvent.model_validate(event)
        payload = value.model_dump_json()
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("call_event_payload_too_large")
        with self._connection(writing=True, busy_timeout_ms=busy_timeout_ms) as db:
            _ready(db)
            cutoff = self._cutoff()
            previous = db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM call_events WHERE event_id=?",
                (str(value.event_id),),
            ).fetchone()
            if previous is not None:
                if _event(previous) != value:
                    raise CallEventConflict()
            else:
                db.execute(
                    "INSERT INTO call_events(event_id,occurred_at,host_id,payload) "
                    "VALUES (?,?,?,?)",
                    (
                        str(value.event_id),
                        _timestamp(value.occurred_at),
                        value.host_id,
                        payload,
                    ),
                )
            self._prune(db, cutoff)
            return previous is None

    def record_feedback(
        self, search_event_id: UUID, value: ExplicitRetrievalFeedback
    ) -> bool | None:
        """Append one local-owner explicit label; False means the identical label exists.

        The source search must still be retained, and every selected reference
        must be one of its originally returned, versioned references. Retention
        and exact clear still govern this row exactly as they govern searches.
        """
        source = self.get(search_event_id)
        if source is None:
            return None
        if source.stage != "core_result":
            raise ValueError("call_event_feedback_source_must_be_search")
        feedback = _feedback_event(source, value)
        return self.record(feedback)

    def feedback_retention_until(self, *, retention_days: int) -> datetime:
        days = _Retention(
            retention_days=retention_days, max_events=self._retention.max_events
        ).retention_days
        return _now() + timedelta(days=days)

    def get(self, event_id: UUID) -> CallEvent | None:
        """Read a retained ID without initializing or pruning any data."""
        identity = _UUID.validate_python(event_id)
        with self._connection() as db:
            _ready(db)
            cutoff = self._cutoff()
            row = db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM call_events WHERE event_id=?", (str(identity),)
            ).fetchone()
            if row is None:
                return None
            value = _event(row)
            return value if _timestamp(value.occurred_at) >= cutoff else None

    def mark_delivery(
        self,
        event_id: UUID,
        *,
        delivery: CallDelivery,
        host_session_id: str,
        injection_elapsed_ms: float,
    ) -> bool | None:
        """Set the host-observed delivery once without claiming adoption.

        True means this report is the stored outcome, False means a prior
        outcome remains authoritative, and None means retention already won.
        """
        identity = _UUID.validate_python(event_id)
        if delivery == "unknown":
            raise ValueError("call_event_delivery_immutable_unknown")
        update = CallEvent.model_validate(
            {
                "event_id": identity,
                "occurred_at": _now(),
                "host_id": None,
                "installation_id": None,
                "tool": "om_recall",
                "state": "empty",
                "stage": "core_result",
                "delivery": delivery,
                "adoption": "unknown",
                "host_session_id": host_session_id,
                "injection_elapsed_ms": injection_elapsed_ms,
                "elapsed_ms": 0,
                "raw_count": 0,
                "memory_count": 0,
                "references": (),
            }
        )
        with self._connection(writing=True) as db:
            _ready(db)
            cutoff = self._cutoff()
            row = db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM call_events WHERE event_id=?",
                (str(identity),),
            ).fetchone()
            if row is None:
                return None
            current = _event(row)
            if _timestamp(current.occurred_at) < cutoff:
                db.execute("DELETE FROM call_events WHERE event_id=?", (str(identity),))
                return None
            if current.delivery != "unknown":
                return (
                    current.delivery == update.delivery
                    and current.host_session_id == update.host_session_id
                    and current.injection_elapsed_ms == update.injection_elapsed_ms
                )
            updated = current.model_copy(
                update={
                    "delivery": update.delivery,
                    "host_session_id": update.host_session_id,
                    "injection_elapsed_ms": update.injection_elapsed_ms,
                }
            )
            payload = CallEvent.model_validate(updated).model_dump_json()
            if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError("call_event_payload_too_large")
            db.execute(
                "UPDATE call_events SET payload=? WHERE event_id=?",
                (payload, str(identity)),
            )
            self._prune(db, cutoff)
            return True

    def list(
        self, *, limit: int = 100, offset: int = 0, host_id: HostId | None = None
    ) -> CallEventPage:
        """Count and items share a snapshot and cutoff; None means no host filter."""
        query = _PageQuery(limit=limit, offset=offset, host_id=host_id)
        with self._connection() as db:
            _ready(db)
            cutoff = self._cutoff()
            where = "occurred_at >= ?"
            parameters: list[str | int] = [cutoff]
            if query.host_id is not None:
                where += " AND host_id = ?"
                parameters.append(query.host_id)
            total = db.execute(
                f"SELECT count(*) FROM call_events WHERE {where}", parameters
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM call_events WHERE {where} "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT ? OFFSET ?",
                [*parameters, query.limit, query.offset],
            ).fetchall()
            return CallEventPage(total=total, items=tuple(_event(row) for row in rows))

    def clear(self, *, expected_event_ids: builtins.list[UUID]) -> int:
        """Delete only the bounded exact ID set, never a filtered or implicit all-clear."""
        identities = _CLEAR_IDS.validate_python(expected_event_ids)
        with self._connection(writing=True) as db:
            _ready(db)
            deleted = 0
            for identity in set(identities):
                deleted += db.execute(
                    "DELETE FROM call_events WHERE event_id=?", (str(identity),)
                ).rowcount
            return deleted

    def clear_all(self) -> int:
        """Delete every call_events row. Evidence and governance tables are untouched."""
        with self._connection(writing=True) as db:
            _ready(db)
            return db.execute("DELETE FROM call_events").rowcount
