"""SDK-authority Matter reads from reference-only organization records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.hy_memory_bridge import HyMemoryBridge
from one_memory.matters.models import (
    Citation,
    Claim,
    Matter,
    MatterContext,
    MemoryRef,
    SampleRecord,
)


class SdkMatterUnavailable(RuntimeError):
    pass


class SdkMatterStore:
    authority = "sdk"

    def __init__(self, database_path: Path, bridge: HyMemoryBridge) -> None:
        self.database_path = database_path
        self.bridge = bridge

    async def list(self) -> list[Matter]:
        rows = await asyncio.to_thread(self._current_rows)
        return [await self._hydrate(row) for row in rows]

    async def get(self, matter_id: str) -> Matter:
        row = await asyncio.to_thread(self._current_row, matter_id)
        if row is None:
            raise KeyError(matter_id)
        return await self._hydrate(row)

    async def history(self, matter_id: str) -> list[Matter]:
        rows = await asyncio.to_thread(self._history_rows, matter_id)
        if not rows:
            raise KeyError(matter_id)
        return [await self._hydrate(row) for row in rows]

    async def context(self, matter_id: str) -> MatterContext:
        matter = await self.get(matter_id)
        lines = [f"# {matter.title}", matter.purpose, matter.boundary]
        lines.extend(f"- {claim.statement}" for claim in matter.claims)
        return MatterContext(
            matter_id=matter.matter_id,
            version=matter.version,
            availability=matter.availability,
            text="\n".join(line for line in lines if line),
        )

    async def search_contexts(self, query: str) -> list[MatterContext]:
        terms = tuple(term.casefold() for term in query.split() if term)
        found = []
        for matter in await self.list():
            haystack = " ".join(
                (
                    matter.title,
                    matter.purpose,
                    matter.boundary,
                    *(claim.statement for claim in matter.claims),
                )
            ).casefold()
            if all(term in haystack for term in terms):
                found.append(await self.context(matter.matter_id))
        return found

    async def evidence(self, matter_id: str, raw_id: str) -> SampleRecord:
        row = await asyncio.to_thread(self._current_row, matter_id)
        if row is None:
            raise KeyError(matter_id)
        references = json.loads(str(row["references_json"]))
        locator = next(
            (
                raw_ref
                for claim in references.get("claims", [])
                for raw_ref in claim.get("raw_refs", [])
                if raw_ref.get("raw_id") == raw_id
            ),
            None,
        )
        if not isinstance(locator, dict):
            raise KeyError(raw_id)
        raw = await self.bridge.get_sdk_raw(raw_id)
        if raw is None:
            raise SdkMatterUnavailable("sdk_matter_raw_unavailable")
        start, end = int(locator["start"]), int(locator["end"])
        quote = raw.content[start:end]
        if hashlib.sha256(quote.encode()).hexdigest() != locator.get("quote_hash"):
            raise SdkMatterUnavailable("sdk_matter_raw_changed")
        role = str(raw.custom.get("role") or "user")
        return SampleRecord(
            evidence_id=None,
            raw_id=raw.raw_id,
            item_index=int(locator.get("item_index") or 0),
            source_id=raw.source,
            conversation_id=raw.session_id,
            role=role,
            written_by=str(raw.custom.get("written_by") or ""),
            created_at=_timestamp(raw.memory_at),
            observed_at=_timestamp(raw.gmt_created) or str(row["created_at"]),
            revision=None,
            content_hash=hashlib.sha256(raw.content.encode()).hexdigest(),
            project=None,
            workspace=(
                str(raw.custom["workspace"])
                if isinstance(raw.custom.get("workspace"), str)
                else None
            ),
            text=quote,
        )

    def _current_rows(self) -> tuple[sqlite3.Row, ...]:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            return tuple(
                database.execute(
                    "SELECT projected.* FROM matters current "
                    "JOIN matter_sdk_versions projected "
                    "ON projected.matter_id=current.matter_id "
                    "AND projected.version=current.version "
                    "ORDER BY projected.matter_id"
                )
            )

    def _current_row(self, matter_id: str) -> sqlite3.Row | None:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            return database.execute(
                "SELECT projected.* FROM matters current "
                "JOIN matter_sdk_versions projected "
                "ON projected.matter_id=current.matter_id "
                "AND projected.version=current.version "
                "WHERE projected.matter_id=?",
                (matter_id,),
            ).fetchone()

    def _history_rows(self, matter_id: str) -> tuple[sqlite3.Row, ...]:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            return tuple(
                database.execute(
                    "SELECT * FROM matter_sdk_versions WHERE matter_id=? "
                    "ORDER BY version",
                    (matter_id,),
                )
            )

    async def _hydrate(self, row: sqlite3.Row) -> Matter:
        references = json.loads(str(row["references_json"]))
        claims = []
        for item in references.get("claims", []):
            memory_refs = []
            statements = []
            for reference in item.get("memory_refs", []):
                memory = await self.bridge.get_sdk_memory(str(reference["memory_id"]))
                if (
                    memory is None
                    or memory.status != "active"
                    or not memory.is_latest
                    or hashlib.sha256(memory.content.encode()).hexdigest()
                    != reference.get("content_hash")
                ):
                    raise SdkMatterUnavailable("sdk_matter_memory_changed")
                statements.append(memory.content)
                memory_refs.append(
                    MemoryRef(
                        memory_id=memory.memory_id,
                        revision=None,
                        status="active",
                        content_hash=str(reference["content_hash"]),
                        authority="sdk",
                        origin=memory.owner or "extracted",
                    )
                )
            citations = []
            for raw_ref in item.get("raw_refs", []):
                raw = await self.bridge.get_sdk_raw(str(raw_ref["raw_id"]))
                if raw is None:
                    raise SdkMatterUnavailable("sdk_matter_raw_unavailable")
                start, end = int(raw_ref["start"]), int(raw_ref["end"])
                quote = raw.content[start:end]
                if hashlib.sha256(quote.encode()).hexdigest() != raw_ref.get(
                    "quote_hash"
                ):
                    raise SdkMatterUnavailable("sdk_matter_raw_changed")
                citations.append(
                    Citation(
                        evidence_id=None,
                        raw_id=raw.raw_id,
                        item_index=int(raw_ref.get("item_index") or 0),
                        quote=quote,
                        source_id=raw.source,
                        written_by=str(raw.custom.get("written_by") or ""),
                        created_at=_timestamp(raw.memory_at),
                    )
                )
            claims.append(
                Claim(
                    statement="\n".join(statements),
                    kind=item["kind"],
                    scope=str(item.get("scope") or ""),
                    citations=citations,
                    basis=item.get("basis") or "unknown",
                    verification=item.get("verification") or "citation_checked",
                    state=item.get("state") or "unresolved",
                    change_note=str(item.get("change_note") or ""),
                    memory_refs=memory_refs,
                )
            )
        return Matter(
            matter_id=str(row["matter_id"]),
            version=int(row["version"]),
            title=str(references.get("title") or "来源不可用"),
            purpose=str(references.get("purpose") or ""),
            boundary=str(references.get("boundary") or ""),
            anchors=list(references.get("anchors") or []),
            claims=claims,
            tags=list(references.get("tags") or []),
            uncertainties=list(references.get("uncertainties") or []),
            scope_confirmed=bool(references.get("scope_confirmed", False)),
            agent_access=bool(references.get("agent_access", False)),
            availability="ready",
            model=str(references.get("model") or "sdk"),
            updated_at=str(row["created_at"]),
            source_count=len(
                {
                    citation.source_id
                    for claim in claims
                    for citation in claim.citations
                }
            ),
            evidence_count=sum(len(claim.citations) for claim in claims),
            source_engine="hy-memory",
        )


def _timestamp(value: int | None) -> str | None:
    return (
        datetime.fromtimestamp(value, tz=UTC).isoformat()
        if value is not None
        else None
    )
