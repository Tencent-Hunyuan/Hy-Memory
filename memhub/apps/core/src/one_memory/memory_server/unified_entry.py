"""The shared intake adapter for entries that already have durable Evidence.

Scanner/import callbacks and manual notes reach the write boundary with an
``EvidenceRecord``, not with a pre-built ``WriteRequest``.  Leaving each entry
to construct its own batch identity is how the durable queue stayed a side
path: the callback could still call ``enqueue_evidence`` directly and the
Evidence→queue crash window stayed open.

This adapter is deliberately small.  It turns one committed Evidence record
into the one-record complete-turn request, submits it once under an evidence
derived idempotency key, and returns the durable record.  The existing
``DurableWriteDrain`` remains the only consumer; it calls the SDK write
coordinator once and persists only the public receipt ids.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal

from one_memory.imports.models import EvidenceOrigin, EvidenceRecord
from one_memory.memory_server.contracts import (
    BatchIdentity,
    WriteRequest,
    internal_session_id_for,
)
from one_memory.memory_server.durable_queue import DurableWriteQueue, DurableWriteRecord

WritePriority = Literal["live", "normal"]


@dataclass(frozen=True)
class UnifiedWriteReceipt:
    """What one intake attempt tells the caller and the composition root."""

    record: DurableWriteRecord
    evidence_id: str
    request_id: str
    enqueued: bool


class UnifiedWriteIntake:
    """Submit already-committed Evidence to the durable queue.

    ``first_round``/``last_round`` are supplied by the caller because the
    complete-turn decision belongs to the scanner/sessionizer (or to the
    manual-note path), not to this adapter.  The key is derived from the
    Evidence id, so a retry after a crash returns the original row instead of
    creating a second HY call.
    """

    def __init__(
        self,
        queue: DurableWriteQueue,
        *,
        source_writes_permitted: Callable[[str], bool] | None = None,
    ) -> None:
        self.queue = queue
        self.source_writes_permitted = source_writes_permitted

    def submit_evidence(
        self,
        record: EvidenceRecord,
        *,
        first_round: int,
        last_round: int,
        priority: WritePriority = "normal",
    ) -> UnifiedWriteReceipt:
        if first_round < 1 or last_round < first_round:
            raise ValueError("round range must be ascending and start at 1 or later")
        if priority not in {"live", "normal"}:
            raise ValueError("priority must be 'live' or 'normal'")
        if self.source_writes_permitted is not None and not self.source_writes_permitted(
            record.source_id
        ):
            # Freeze is the product's one Harness processing decision. Refuse
            # new intake so a frozen source creates no new durable write row;
            # Evidence and already-pending jobs remain untouched.
            raise SourceWriteDisabledError(record.evidence_id)
        # A `chat_exports` branch id (`<conversation>:branch:<hex>`) or a long
        # source conversation id can exceed the contract's 128-char bound; pass
        # it through the one shared derivation so the whole import is not
        # rejected. Values that already fit keep their exact identity.
        request = self._evidence_request(
            record,
            first_round=first_round,
            last_round=last_round,
            priority=priority,
        )
        durable = self.queue.submit(request)
        return UnifiedWriteReceipt(
            record=durable,
            evidence_id=record.evidence_id,
            request_id=durable.request_id,
            enqueued=durable.enqueued,
        )

    def readmit_cancelled_evidence(
        self,
        record: EvidenceRecord,
        *,
        job_id: str,
        first_round: int,
        last_round: int,
        priority: WritePriority = "normal",
    ) -> int:
        request = self._evidence_request(
            record,
            first_round=first_round,
            last_round=last_round,
            priority=priority,
        )
        return self.queue.readmit_cancelled_permission_confirmed(job_id, request)

    @staticmethod
    def _evidence_request(
        record: EvidenceRecord,
        *,
        first_round: int,
        last_round: int,
        priority: WritePriority,
    ) -> WriteRequest:
        session_id = internal_session_id_for(record.conversation_id, record.source_id)
        event_at, time_quality = _job_time((record,))
        request_key = (
            f"entry:{record.evidence_id}"
            if record.revision == 1
            else (f"entry:v2:{record.evidence_id}:{record.revision}:{record.content_hash}")
        )
        return WriteRequest(
            internal_session_id=session_id,
            source_id=record.source_id,
            batch_identity=BatchIdentity(
                internal_session_id=session_id,
                first_round=first_round,
                last_round=last_round,
            ),
            evidence_ids=[record.evidence_id],
            input_records=(record,),
            idempotency_key=request_key,
            priority=priority,
            event_at=event_at,
            time_quality=time_quality,
        )

    def submit_records(
        self,
        records: tuple[EvidenceRecord, ...],
        *,
        priority: WritePriority = "normal",
    ) -> UnifiedWriteReceipt:
        if not records:
            raise ValueError("records must not be empty")
        first = records[0]
        identity = (first.user_id, first.source_id, first.conversation_id, first.workspace)
        if any(
            (record.user_id, record.source_id, record.conversation_id, record.workspace) != identity
            for record in records
        ):
            raise ValueError("records must belong to one conversation")
        if self.source_writes_permitted is not None and not self.source_writes_permitted(
            first.source_id
        ):
            raise SourceWriteDisabledError(first.evidence_id)
        material = ":".join(
            f"{record.evidence_id}:{record.revision}:{record.content_hash}" for record in records
        )
        request_id = f"turn:v1:{sha256(material.encode()).hexdigest()}"
        session_id = internal_session_id_for(first.conversation_id, first.source_id)
        event_at, time_quality = _job_time(records)
        durable = self.queue.submit(
            WriteRequest(
                internal_session_id=session_id,
                source_id=first.source_id,
                batch_identity=BatchIdentity(
                    internal_session_id=session_id,
                    first_round=1,
                    last_round=1,
                ),
                evidence_ids=[record.evidence_id for record in records],
                input_records=records,
                idempotency_key=request_id,
                priority=priority,
                event_at=event_at,
                time_quality=time_quality,
            )
        )
        return UnifiedWriteReceipt(
            record=durable,
            evidence_id=first.evidence_id,
            request_id=durable.request_id,
            enqueued=durable.enqueued,
        )


class SourceWriteDisabledError(RuntimeError):
    """A committed Evidence record whose Harness is frozen."""

    def __init__(self, evidence_id: str) -> None:
        super().__init__("model_content_source_disabled")
        self.evidence_id = evidence_id


def _job_time(
    records: tuple[EvidenceRecord, ...],
) -> tuple[datetime, Literal["exact", "anchored", "session", "approximate"]]:
    """Use the turn opener's wall clock, with an explicit fallback quality."""

    opener = next(
        (record for record in records if record.role == "user"),
        records[0],
    )
    if opener.created_at is None:
        return opener.imported_at, "approximate"
    file_origin = opener.origin_meta.get(EvidenceOrigin.FILE)
    basis = file_origin.raw_meta.get("time_basis") if file_origin is not None else None
    turn_event_at = file_origin.raw_meta.get("turn_event_at") if file_origin is not None else None
    if isinstance(turn_event_at, str):
        try:
            return datetime.fromisoformat(turn_event_at), "exact"
        except ValueError:
            pass
    if basis == "file_mtime":
        return opener.created_at, "session"
    if EvidenceOrigin.HOOK in opener.origins:
        return opener.created_at, "exact"
    return opener.created_at, "exact"
