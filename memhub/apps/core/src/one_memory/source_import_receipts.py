"""Measured local import coverage, without another extraction or host scan."""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from one_memory.database_access import main_database, register_function
from one_memory.source_query_preview import display_query_projection


class SourceReceiptExample(BaseModel):
    evidence_id: str
    role: str
    written_by: str
    created_at: datetime | None
    excerpt: str
    truncated: bool


class SourceQueryExample(SourceReceiptExample):
    evidence_revision: int = Field(ge=1)
    query_start: int = Field(ge=0)
    query_end: int = Field(ge=0)
    projection_version: int


class SourceImportReceipt(BaseModel):
    source_id: str
    measured_at: datetime
    messages: int = Field(ge=0)
    conversations: int = Field(ge=0)
    person_messages: int = Field(ge=0)
    agent_messages: int = Field(ge=0)
    tool_messages: int = Field(ge=0)
    unknown_author_messages: int = Field(ge=0)
    unknown_time_messages: int = Field(ge=0)
    examples: tuple[SourceReceiptExample, ...]
    query_examples: tuple[SourceQueryExample, ...] = ()


def build_source_import_receipt(
    database_path: Path, *, user_id: str, source_id: str, query_evidence_id: str | None = None
) -> SourceImportReceipt:
    with main_database(database_path).read(busy_timeout_ms=3_000) as db:
        deadline = time.monotonic() + 8
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.row_factory = sqlite3.Row
        # A writer may be importing concurrently. Counts and examples describe one snapshot.
        db.execute("BEGIN")
        measured_at = datetime.now(UTC)
        totals = db.execute(
            "SELECT count(*) AS messages, "
            "count(DISTINCT conversation_id || char(0) || COALESCE(workspace, '')) "
            "AS conversations, "
            "coalesce(sum(role='user'),0) AS person_messages, "
            "coalesce(sum(role='assistant'),0) AS agent_messages, "
            "coalesce(sum(role='tool'),0) AS tool_messages, "
            "coalesce(sum(role NOT IN ('user','assistant','tool')),0) "
            "AS unknown_author_messages, "
            "coalesce(sum(created_at IS NULL),0) AS unknown_time_messages "
            "FROM evidence WHERE user_id=? AND source_id=?",
            (user_id, source_id),
        ).fetchone()
        # Bound text reads before materializing snippets. These are samples, not a
        # recency ranking; sorting every large original body is unnecessary here.
        examples = db.execute(
            "SELECT evidence_id, role, CASE role "
            "WHEN 'user' THEN 'person' WHEN 'assistant' THEN 'agent' "
            "WHEN 'tool' THEN 'tool' ELSE 'unknown' END AS written_by, created_at, "
            "substr(verbatim,1,600) AS excerpt, length(verbatim)>600 AS truncated "
            "FROM evidence WHERE evidence_id IN ("
            "SELECT evidence_id FROM evidence WHERE user_id=? AND source_id=? LIMIT 3)",
            (user_id, source_id),
        ).fetchall()
        register_function(
            db, "display_query_projection", 3, display_query_projection, deterministic=True
        )
        # Qualify before LIMIT: agent/system rows must not consume the three samples.
        query_filter = " AND evidence_id=?" if query_evidence_id is not None else ""
        queries = db.execute(
            f"""SELECT evidence_id, revision AS evidence_revision, role,
                CASE role WHEN 'user' THEN 'person' WHEN 'assistant' THEN 'agent'
                    WHEN 'tool' THEN 'tool' ELSE 'unknown' END AS written_by,
                created_at,
                CASE WHEN user_id=? AND source_id=? AND role='user'
                    AND length(verbatim)<=65536
                    THEN display_query_projection(verbatim,role,'person')
                    ELSE NULL END AS projection
                FROM evidence WHERE user_id=? AND source_id=?{query_filter}
                    AND projection IS NOT NULL
                ORDER BY imported_at DESC, evidence_id LIMIT 3""",
            (user_id, source_id, user_id, source_id)
            + ((query_evidence_id,) if query_evidence_id is not None else ()),
        ).fetchall()
        return SourceImportReceipt(
            source_id=source_id,
            measured_at=measured_at,
            **dict(totals),
            examples=tuple(SourceReceiptExample(**dict(row)) for row in examples),
            query_examples=tuple(
                SourceQueryExample(
                    **{key: value for key, value in dict(row).items() if key != "projection"},
                    **json.loads(row["projection"]),
                )
                for row in queries
            ),
        )
