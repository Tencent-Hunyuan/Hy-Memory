"""A3-01/A3-02: the in-memory stand-in for the durable write registry.

The durable queue itself is A2's MySQL table. Until that lands, entries still
need a request id they can hand back and query later — that is the whole
contract A3 promises ("returns a durable request ID"), and it is what lets a
client ask "did that write happen?" instead of retrying blindly.

This registry is deliberately not a queue: it records *acceptance and outcome*,
not pending work. Confusing the two would let a caller believe "recorded" means
"done".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from one_memory.memory_server.contracts import WriteRequest
from one_memory.memory_server.submit import (
    WriteConflictError,
    payload_fingerprint,
    submit_write,
)

WriteState = Literal[
    "accepted",
    "completed",
    "failed",
    "unknown",
    "cancelled",
    "waiting_for_permission",
    "waiting_for_model",
]

#: States a caller may still see change. `unknown` is in here on purpose: it is
#: the state that still needs resolving, so treating it as final would be wrong.
NON_TERMINAL: frozenset[WriteState] = frozenset(
    {
        "accepted",
        "unknown",
        # Parked, not finished: a later admission pass may still run them.
        "waiting_for_permission",
        "waiting_for_model",
    }
)


@dataclass(frozen=True)
class WriteRecord:
    request_id: str
    state: WriteState
    fingerprint: str
    created_at: str
    updated_at: str
    detail: str = ""
    raw_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WriteRegistry:
    """Records accepted writes by idempotency key, and their outcome."""

    def __init__(self) -> None:
        self._records: dict[str, WriteRecord] = {}
        self._lock = threading.Lock()

    def submit(
        self, request: WriteRequest, known: dict[str, str] | None = None
    ) -> tuple[WriteRecord, bool]:
        """Accept a write, or reconcile it against one already on record.

        Returns (record, enqueued). A conflicting reuse raises, so a caller
        cannot silently replace an earlier intent.
        """
        fingerprint = payload_fingerprint(request)
        table = (
            known
            if known is not None
            else {key: record.fingerprint for key, record in self._records.items()}
        )
        submission = submit_write(request, known=table)

        with self._lock:
            existing = self._records.get(request.request_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise WriteConflictError(
                        f"idempotency key {request.request_key} already exists "
                        "with different content"
                    )
                return existing, False

            record = WriteRecord(
                request_id=request.request_key,
                state="accepted",
                fingerprint=fingerprint,
                created_at=_now(),
                updated_at=_now(),
            )
            self._records[request.request_key] = record
            return record, submission.enqueued

    def get(self, request_id: str) -> WriteRecord | None:
        with self._lock:
            return self._records.get(request_id)

    def set_state(self, request_id: str, state: WriteState, detail: str = "") -> WriteRecord | None:
        """Record an outcome. Unknown writes return None rather than creating a row.

        Inventing a record for an unknown id would make "we never accepted this"
        look like "we accepted it and it failed".

        A terminal record also refuses to move back to a live state. This
        registry shares that rule with the durable one: letting a `completed`
        record become `accepted` would let a blind re-send duplicate a write
        that already landed.
        """
        with self._lock:
            existing = self._records.get(request_id)
            if existing is None:
                return None
            if existing.state not in NON_TERMINAL and state in NON_TERMINAL:
                return None
            updated = WriteRecord(
                request_id=existing.request_id,
                state=state,
                fingerprint=existing.fingerprint,
                created_at=existing.created_at,
                updated_at=_now(),
                detail=detail,
            )
            self._records[request_id] = updated
            return updated
