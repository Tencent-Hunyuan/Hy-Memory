"""Apply explicit source revocations without deleting evidence or governance history."""

import time
from collections.abc import Sequence
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.imports.models import EvidenceRecord
from one_memory.memory_evidence_freshness import (
    freshness_proofs_prefetched,
    register_memory_freshness,
)
from one_memory.memory_governance import GovernedMemory
from one_memory.memory_visibility import mode as visibility_mode
from one_memory.memory_visibility import readable_ids

# A memory is readable while every active citation is: still current, owned by
# the reader, and not on a revoked source. `+link.active` keeps SQLite on the
# (memory_id, evidence_id) primary key. Left on its own, SQLite picks the
# (active, evidence_id, memory_id) index and walks every active link in the
# library once per memory, which on a 1,226-memory library took 5.4 s for 500
# memories and hit the 5 s interrupt, so the memory library and the memory map
# both answered 503 (D78 missed this path).
_MEMORY_READABLE = """
    is_memory_evidence_current(?,?,?) AND NOT EXISTS (
        SELECT 1 FROM governed_memory_evidence link
        LEFT JOIN evidence e ON e.evidence_id=link.evidence_id
        LEFT JOIN source_grants g ON g.user_id=e.user_id AND g.source_id=e.source_id
        WHERE link.memory_id=? AND +link.active=1
          AND (e.evidence_id IS NULL OR e.user_id!=? OR g.revoked_at IS NOT NULL)
    )
"""


class SourceReadAccess:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _revoked_evidence(self, evidence_ids: Sequence[str]) -> set[str]:
        keys = list(dict.fromkeys(evidence_ids))
        blocked: set[str] = set()
        with main_database(self.database_path).read() as db:
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            for start in range(0, len(keys), 900):
                batch = keys[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(
                    "SELECT e.evidence_id FROM evidence e JOIN source_grants g "
                    "ON g.user_id=e.user_id AND g.source_id=e.source_id "
                    f"WHERE g.revoked_at IS NOT NULL AND e.evidence_id IN ({placeholders})",
                    batch,
                )
                blocked.update(str(row[0]) for row in rows)
        return blocked

    def memories(self, memories: Sequence[GovernedMemory]) -> list[GovernedMemory]:
        return self.filter(memories)

    def revoked_free(self, memories: Sequence[GovernedMemory]) -> list[GovernedMemory]:
        """Drop memories a revoked source supplied, without per-row checks.

        For counts. D22 settled that a total does not run the row-by-row
        freshness check, which scales with the whole library; a revoked
        source is still excluded, in one batched lookup rather than one
        statement per memory.
        """
        blocked = self._revoked_evidence([key for item in memories for key in item.evidence_ids])
        if not blocked:
            return list(memories)
        return [item for item in memories if blocked.isdisjoint(item.evidence_ids)]

    def filter(self, memories: Sequence[GovernedMemory]) -> list[GovernedMemory]:
        blocked = self._revoked_evidence([key for item in memories for key in item.evidence_ids])
        # One surviving citation does not prove every clause in a memory.
        candidates = [item for item in memories if blocked.isdisjoint(item.evidence_ids)]
        if not candidates:
            return []
        with main_database(self.database_path).read() as db:
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            db.execute("BEGIN")
            register_memory_freshness(db)
            # D93 A: a memory whose visibility is already known and nothing has
            # touched since is answered from that conclusion. The rest are
            # checked exactly, as they are today — so a cache that is still
            # being filled never misinforms, it only helps less.
            if visibility_mode() != "exact":
                known = readable_ids(db)
                decided = [item for item in candidates if item.memory_id in known]
                unknown = [item for item in candidates if item.memory_id not in known]
            else:
                decided, unknown = [], candidates
            if not unknown:
                return decided
            # D90: the freshness UDF reads its proofs per memory, and that is
            # what makes this scale with the page — 0.69 s of 0.77 s on 160
            # memories, and enough to cross the five second interrupt on both
            # `/api/v1/memories` and the map while the library is also being
            # written to. Read them once for the whole page instead; the
            # decisions taken from them are the same rows in the same
            # transaction.
            with freshness_proofs_prefetched(
                db,
                [item.memory_id for item in unknown],
                evidence_ids=[evidence_id for item in unknown for evidence_id in item.evidence_ids],
            ):
                checked = [
                    item
                    for item in unknown
                    if db.execute(
                        f"SELECT {_MEMORY_READABLE}",
                        (
                            item.memory_id,
                            item.revision,
                            item.user_id,
                            item.memory_id,
                            item.user_id,
                        ),
                    ).fetchone()[0]
                ]
            # Order is the caller's; the conclusion only shortcuts the check.
            order = {item.memory_id: index for index, item in enumerate(candidates)}
            return sorted(decided + checked, key=lambda item: order[item.memory_id])

    def records(self, records: Sequence[EvidenceRecord]) -> list[EvidenceRecord]:
        blocked = self._revoked_evidence([item.evidence_id for item in records])
        return [item for item in records if item.evidence_id not in blocked]

    def has_revoked_sources(self) -> bool:
        with main_database(self.database_path).read() as db:
            return (
                db.execute(
                    "SELECT 1 FROM source_grants WHERE revoked_at IS NOT NULL LIMIT 1"
                ).fetchone()
                is not None
            )

    def revoked_source_ids(self, *, user_id: str) -> frozenset[str]:
        with main_database(self.database_path).read() as db:
            return frozenset(
                str(row[0])
                for row in db.execute(
                    "SELECT source_id FROM source_grants "
                    "WHERE user_id=? AND revoked_at IS NOT NULL",
                    (user_id,),
                )
            )
