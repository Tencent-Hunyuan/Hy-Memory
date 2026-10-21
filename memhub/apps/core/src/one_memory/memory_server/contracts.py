"""A0-02: the frozen cross-layer write contract.

Four identities have to be stable before any of them is stored in MySQL,
because everything downstream keys off them:

- ``SessionIdentity``: versioned internal id derived from
  ``(harness_id, external_session_id)``. A Harness' own id is a display
  label; two harnesses may emit the same one, so the pair is the identity.
- ``BatchIdentity``: deterministic id for one five-round batch, so a replayed
  scan can recognise "already enqueued" without a second source of truth.
- ``WriteRequest``: what an entry submits. Evidence must already exist.
- ``WriteJobState``: the state machine, including ``unknown``.

Session ids are hashed with a local key rather than stored raw. The value is
used to join rows and to detect "same session", both of which work on the
hash; keeping the raw Harness id in MemHub's own tables would add a usable
cross-reference to host data that nothing needs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from one_memory.imports.models import EvidenceRecord

#: Bumped when a derivation rule changes, so old and new ids never compare equal.
SCHEMA_VERSION: Final[str] = "ms1"

_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f0b6f4a-2f6b-5c1e-9a4f-7c2f0d1a8b33")


def _local_key() -> bytes:
    """Per-installation key for HMAC derivations.

    Generated once and cached for the process. A test or a single shot may
    override it through ``ONE_MEMORY_SESSION_KEY`` so ids are reproducible in
    fixtures. It is not a secret with an attacker model behind it; its job is
    to keep stored ids from being a plaintext index of host session ids.
    """
    override = os.environ.get("ONE_MEMORY_SESSION_KEY")
    if override:
        return override.encode()
    return b"one-memory-local-session-key-v1"


def _hmac_hex(value: str) -> str:
    return hmac.new(_local_key(), value.encode(), hashlib.sha256).hexdigest()


def session_id(*, harness_id: str, external_session_id: str) -> str:
    """Versioned internal session id for a harness/external pair."""
    material = f"{SCHEMA_VERSION}|{harness_id}|{external_session_id}"
    return f"{SCHEMA_VERSION}_{uuid.uuid5(_NAMESPACE, material).hex}"


def batch_id(*, internal_session_id: str, first_round: int, last_round: int) -> str:
    """Deterministic id for one batch of rounds within a session."""
    material = f"{SCHEMA_VERSION}|{internal_session_id}|{first_round}|{last_round}"
    return f"{SCHEMA_VERSION}_{uuid.uuid5(_NAMESPACE, material).hex}"


#: `internal_session_id` is bounded to 128 characters by this contract, but an
#: Evidence `conversation_id` may be up to 512 (`imports.models`) and a
#: `chat_exports` branch id appends `:branch:<16 hex>`. A raw conversation id
#: therefore can never be passed straight through. Values that already fit keep
#: their exact identity; only a value that would have been rejected is derived.
INTERNAL_SESSION_ID_MAX = 128


def internal_session_id_for(conversation_id: str, source_id: str) -> str:
    """Normalise a conversation identity to the durable ``internal_session_id``.

    This is the single derivation shared by every Evidence-first intake path.
    A conversation id that already fits the contract is returned unchanged:
    every durable row written before this helper existed stored that exact
    value, so preserving it keeps `payload_json`, `session_key`, `batch_id` and
    the idempotency key byte-identical (no migration, no replay conflict, no
    per-session serialisation split). Only a value that the contract would have
    rejected before it ever reached the queue is replaced by the versioned
    session id, which is deterministic for a given (source, conversation).
    """
    if len(conversation_id) <= INTERNAL_SESSION_ID_MAX:
        return conversation_id
    derived = session_id(harness_id=source_id, external_session_id=conversation_id)
    if len(derived) > INTERNAL_SESSION_ID_MAX:
        raise ValueError("derived internal_session_id exceeds the contract bound")
    return derived


class SessionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    internal_session_id: str = Field(min_length=1, max_length=128)
    harness_id: str = Field(min_length=1, max_length=128)
    #: HMAC of the harness' own id. Empty when the harness gave none.
    external_session_id_hmac: str = Field(min_length=0, max_length=64)
    #: Human-readable label only. Never used to join or deduplicate.
    display_key: str = Field(min_length=0, max_length=512)

    @classmethod
    def derive(
        cls,
        *,
        harness_id: str,
        external_session_id: str | None,
        source_file_key: str,
    ) -> SessionIdentity:
        """Derive a stable session identity.

        A harness that emits no session id still gets one, derived from the
        file it came from. Without that, every scan would invent a fresh
        session and a five-round batch could never accumulate.
        """
        if external_session_id:
            hmac_value = _hmac_hex(external_session_id)
            display = f"{harness_id}_{external_session_id}"
            internal = session_id(harness_id=harness_id, external_session_id=external_session_id)
        else:
            hmac_value = ""
            display = f"{harness_id}_file_{source_file_key[:12]}"
            internal = session_id(
                harness_id=harness_id, external_session_id=f"file:{source_file_key}"
            )
        return cls(
            internal_session_id=internal,
            harness_id=harness_id,
            external_session_id_hmac=hmac_value,
            display_key=display,
        )


class BatchIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    internal_session_id: str = Field(min_length=1, max_length=128)
    first_round: int = Field(ge=1)
    last_round: int = Field(ge=1)

    @field_validator("last_round")
    @classmethod
    def _rounds_ordered(cls, value: int, info) -> int:  # type: ignore[no-untyped-def]
        first = info.data.get("first_round")
        if first is not None and value < first:
            raise ValueError("last_round must not precede first_round")
        return value

    @property
    def batch_id(self) -> str:
        return batch_id(
            internal_session_id=self.internal_session_id,
            first_round=self.first_round,
            last_round=self.last_round,
        )


class CanonicalMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harness_id: str = Field(min_length=1, max_length=128)
    external_session_id: Annotated[str, Field(min_length=0, max_length=512)] = ""
    message_id: str | None = Field(default=None, min_length=1, max_length=512)
    ordinal: int = Field(ge=0)
    role: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    workspace: str | None = Field(default=None, max_length=1024)

    @field_validator("ordinal")
    @classmethod
    def _ordinal_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("ordinal must be >= 0")
        return value


class WriteJobState(StrEnum):
    """Task states for the durable write queue.

    ``UNKNOWN`` is not an error state, it is the honest record of "we asked HY
    and cannot prove the outcome". Anything else either duplicates a memory by
    retrying blindly or loses one by giving up.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    DEAD = "dead"

    @property
    def is_terminal(self) -> bool:
        return self in {
            WriteJobState.COMPLETED,
            WriteJobState.FAILED,
            WriteJobState.CANCELLED,
            WriteJobState.DEAD,
        }


class WriteRequest(BaseModel):
    """What an entry submits to the Memory Server. Never a direct HY call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    internal_session_id: str = Field(min_length=1, max_length=128)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    batch_identity: BatchIdentity
    evidence_ids: list[str] = Field(default_factory=list)
    raw_ids: list[str] = Field(default_factory=list)
    # New jobs freeze the complete input in their own payload. The default is
    # solely for reading pre-upgrade rows whose payload only carried ids.
    input_records: tuple[EvidenceRecord, ...] = ()
    idempotency_key: str = Field(min_length=1, max_length=256)
    priority: Literal["live", "normal"] = "normal"
    event_at: datetime | None = None
    time_quality: Literal["exact", "anchored", "session", "approximate"] = "approximate"

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_required(cls, value: list[str]) -> list[str]:
        if any(not item or not item.strip() for item in value):
            raise ValueError("evidence_ids must not contain empty ids")
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return value

    @field_validator("input_records")
    @classmethod
    def _input_records_match_ids(
        cls, value: tuple[EvidenceRecord, ...], info: ValidationInfo
    ) -> tuple[EvidenceRecord, ...]:
        evidence_ids = info.data.get("evidence_ids")
        if value and evidence_ids != [record.evidence_id for record in value]:
            raise ValueError("input_records must match evidence_ids in order")
        return value

    @field_validator("raw_ids")
    @classmethod
    def _raw_ids_are_bounded_and_unique(cls, value: list[str]) -> list[str]:
        if len(value) > 100:
            raise ValueError("raw_ids may contain at most 100 ids")
        if any(not item or not item.strip() for item in value):
            raise ValueError("raw_ids must not contain empty ids")
        if len(set(value)) != len(value):
            raise ValueError("raw_ids must be unique")
        return value

    @model_validator(mode="after")
    def _one_authoritative_input(self) -> WriteRequest:
        if bool(self.evidence_ids) == bool(self.raw_ids):
            raise ValueError("a write request must point at evidence or SDK raw, not both")
        if self.raw_ids and self.input_records:
            raise ValueError("SDK raw requests must not embed Evidence records")
        return self

    @property
    def request_key(self) -> str:
        """Stable dedup key. Same batch + same intent must not enqueue twice."""
        return self.idempotency_key or self.batch_identity.batch_id
