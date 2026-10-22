"""Memory evidence freshness checks are retired.

Read paths used to re-validate ``memory_task_outputs.support_refs`` against
live ``evidence`` rows via ``is_memory_evidence_current``. That design hid
memories when evidence changed, but at high cost (library 503s, per-row SQL,
segment-key breakage). Call sites still import this module; registration is
now a no-op that always admits the row.

Proof rows may still be written by the ingest path; they are ignored on read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from one_memory.database_access import register_function

#: Historical budget error codes. Retained so diagnostics/env docs do not break
#: mid-teardown; nothing raises them on the read path anymore.
BUDGET_ERRORS = frozenset(
    {
        "memory_freshness_proof_budget",
        "memory_freshness_reference_budget",
        "memory_freshness_evidence_budget",
        "memory_freshness_scope_budget",
    }
)


def budget_errors_raise() -> bool:
    """Former opt-in to raise on budget overrun. Always false after retirement."""
    return False


def has_proof_table(db: sqlite3.Connection) -> bool:
    """Whether ``memory_task_outputs`` exists. Unused by the retired check."""
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_task_outputs'"
        ).fetchone()
        is not None
    )


@contextmanager
def freshness_proofs_prefetched(
    db: sqlite3.Connection,
    memory_ids: Sequence[str],
    *,
    evidence_ids: Sequence[str] = (),
) -> Iterator[None]:
    """No-op. Kept so batched read callers need not change in this PR."""
    del db, memory_ids, evidence_ids
    yield


def register_memory_freshness(db: sqlite3.Connection) -> str:
    """Register a always-true UDF; return SQL predicate ``1``.

    Callers that embed the return value in ``WHERE`` keep working. Callers that
    invoke ``is_memory_evidence_current`` by name also get current.
    """
    register_function(db, "is_memory_evidence_current", 3, lambda *_: 1)
    return "1"
