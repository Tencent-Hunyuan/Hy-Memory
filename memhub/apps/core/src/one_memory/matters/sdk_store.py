"""SDK-authority Matter reads from reference-only organization records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from one_memory.database_access import main_database
from one_memory.hy_memory_bridge import HyMemoryBridge
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.matters.models import (
    Citation,
    Claim,
    Matter,
    MatterContext,
    MatterPatch,
    MemoryRef,
    SampleRecord,
)
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.unified_entry import UnifiedWriteIntake


class SdkMatterUnavailable(RuntimeError):
    pass


class SdkMatterStore:
    authority = "sdk"

    def __init__(self, database_path: Path, bridge: HyMemoryBridge) -> None:
        self.database_path = database_path
        self.bridge = bridge
        self.evidence_store: EvidenceStore | None = None
        self.write_intake: UnifiedWriteIntake | None = None
        self.write_queue: DurableWriteQueue | None = None

    def configure_notes(
        self,
        *,
        evidence_store: EvidenceStore,
        write_intake: UnifiedWriteIntake,
        write_queue: DurableWriteQueue,
    ) -> None:
        self.evidence_store = evidence_store
        self.write_intake = write_intake
        self.write_queue = write_queue

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

    async def patch(self, matter_id: str, patch: MatterPatch) -> Matter:
        await asyncio.to_thread(self._patch_references, matter_id, patch)
        return await self.get(matter_id)

    async def save_references(
        self,
        references: dict[str, object],
        *,
        matter_id: str | None = None,
    ) -> Matter:
        identifier = matter_id or str(
            uuid5(
                NAMESPACE_URL,
                "memhub:matter:sdk:"
                + json.dumps(
                    {
                        "title": references.get("title"),
                        "memory_ids": sorted(
                            str(ref["memory_id"])
                            for claim in references.get("claims", [])
                            if isinstance(claim, dict)
                            for ref in claim.get("memory_refs", [])
                            if isinstance(ref, dict)
                        ),
                    },
                    sort_keys=True,
                ),
            )
        )
        await asyncio.to_thread(self._save_references, identifier, references)
        return await self.get(identifier)

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

    async def source_ids(self, matter_id: str) -> frozenset[str]:
        row = await asyncio.to_thread(self._current_row, matter_id)
        if row is None:
            raise KeyError(matter_id)
        references = json.loads(str(row["references_json"]))
        sources = set()
        for raw_id in {
            str(raw_ref["raw_id"])
            for claim in references.get("claims", [])
            for raw_ref in claim.get("raw_refs", [])
        }:
            raw = await self.bridge.get_sdk_raw(raw_id)
            if raw is None:
                raise SdkMatterUnavailable("sdk_matter_raw_unavailable")
            sources.add(raw.source)
        return frozenset(sources)

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

    async def add_note(
        self,
        identifier: str,
        text: str,
        *,
        expected_version: int,
    ) -> SampleRecord:
        matter = await self.get(identifier)
        if matter.version != expected_version:
            raise ValueError("matter_version_conflict")
        if self.evidence_store is None or self.write_intake is None:
            raise SdkMatterUnavailable("sdk_matter_note_intake_unavailable")
        now = datetime.now(UTC)
        result = await asyncio.to_thread(
            self.evidence_store.put,
            EvidenceInput(
                source_id="one-memory-manual",
                conversation_id=f"matter:{identifier}",
                message_id=str(uuid4()),
                role="user",
                written_by=WrittenBy.PERSON,
                created_at=now,
                verbatim=text,
                project=matter.title,
                origin_meta={
                    EvidenceOrigin.MANUAL_IMPORT: OriginMetadata(
                        raw_meta={"matter_id": identifier}
                    )
                },
            ),
        )
        receipt = await asyncio.to_thread(
            self.write_intake.submit_records,
            (result.record,),
        )
        await asyncio.to_thread(
            self._record_note,
            identifier,
            result.record.evidence_id,
            receipt.request_id,
            now.isoformat(),
        )
        return _sample_from_evidence(result.record)

    async def notes(self, identifier: str) -> list[SampleRecord]:
        await self.get(identifier)
        rows = await asyncio.to_thread(self._note_rows, identifier)
        found = []
        for row in rows:
            request_id = str(row["request_id"])
            durable = self.write_queue.get(request_id) if self.write_queue else None
            if durable is not None and durable.sdk_raw_ids:
                raw = await self.bridge.get_sdk_raw(durable.sdk_raw_ids[0])
                if raw is None:
                    raise SdkMatterUnavailable("sdk_matter_note_raw_unavailable")
                found.append(_sample_from_raw(raw, str(row["created_at"])))
                continue
            record = (
                self.evidence_store.get(str(row["note_id"]))
                if self.evidence_store is not None
                else None
            )
            if record is not None:
                found.append(_sample_from_evidence(record))
        return found

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

    def _record_note(
        self,
        matter_id: str,
        note_id: str,
        request_id: str,
        created_at: str,
    ) -> None:
        with main_database(self.database_path).write(autocommit=True) as database:
            database.execute(
                "INSERT INTO matter_sdk_notes "
                "(matter_id,note_id,request_id,created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(request_id) DO NOTHING",
                (matter_id, note_id, request_id, created_at),
            )

    def _note_rows(self, matter_id: str) -> tuple[sqlite3.Row, ...]:
        with main_database(self.database_path).read(row_factory=sqlite3.Row) as database:
            return tuple(
                database.execute(
                    "SELECT * FROM matter_sdk_notes WHERE matter_id=? ORDER BY created_at",
                    (matter_id,),
                )
            )

    def _patch_references(self, matter_id: str, patch: MatterPatch) -> None:
        with main_database(self.database_path).write(
            autocommit=True,
            row_factory=sqlite3.Row,
        ) as database:
            row = database.execute(
                "SELECT current.version,projected.references_json "
                "FROM matters current JOIN matter_sdk_versions projected "
                "ON projected.matter_id=current.matter_id "
                "AND projected.version=current.version "
                "WHERE current.matter_id=?",
                (matter_id,),
            ).fetchone()
            if row is None:
                raise KeyError(matter_id)
            if int(row["version"]) != patch.expected_version:
                raise ValueError("matter_version_conflict")
            references = json.loads(str(row["references_json"]))
            for key, value in (
                ("title", patch.title),
                ("boundary", patch.boundary),
                ("scope_confirmed", patch.scope_confirmed),
                ("agent_access", patch.agent_access),
            ):
                if value is not None:
                    references[key] = value
            version = patch.expected_version + 1
            now = datetime.now(UTC).isoformat()
            database.execute(
                "INSERT INTO matter_sdk_versions "
                "(matter_id,version,references_json,created_at) VALUES (?,?,?,?)",
                (
                    matter_id,
                    version,
                    json.dumps(references, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            database.execute(
                "UPDATE matters SET version=?,needs_update=0 WHERE matter_id=?",
                (version, matter_id),
            )

    def _save_references(
        self,
        matter_id: str,
        references: dict[str, object],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with main_database(self.database_path).write(
            autocommit=True,
            row_factory=sqlite3.Row,
        ) as database:
            current = database.execute(
                "SELECT version FROM matters WHERE matter_id=?",
                (matter_id,),
            ).fetchone()
            version = int(current["version"]) + 1 if current is not None else 1
            database.execute(
                "INSERT INTO matter_sdk_versions "
                "(matter_id,version,references_json,created_at) VALUES (?,?,?,?)",
                (
                    matter_id,
                    version,
                    json.dumps(references, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            database.execute(
                "INSERT INTO matters(matter_id,version,needs_update) VALUES (?,?,0) "
                "ON CONFLICT(matter_id) DO UPDATE SET "
                "version=excluded.version,needs_update=0",
                (matter_id, version),
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


def _sample_from_evidence(record) -> SampleRecord:
    return SampleRecord(
        evidence_id=record.evidence_id,
        raw_id=None,
        source_id=record.source_id,
        conversation_id=record.conversation_id,
        role=record.role,
        written_by=str(record.written_by),
        created_at=record.created_at.isoformat() if record.created_at else None,
        observed_at=record.imported_at.isoformat(),
        revision=record.revision,
        content_hash=record.content_hash,
        project=record.project,
        workspace=record.workspace,
        text=record.verbatim,
    )


def _sample_from_raw(raw, observed_at: str) -> SampleRecord:
    return SampleRecord(
        evidence_id=None,
        raw_id=raw.raw_id,
        item_index=0,
        source_id=raw.source,
        conversation_id=raw.session_id,
        role=str(raw.custom.get("role") or "user"),
        written_by=str(raw.custom.get("written_by") or ""),
        created_at=_timestamp(raw.memory_at),
        observed_at=observed_at,
        revision=None,
        content_hash=hashlib.sha256(raw.content.encode()).hexdigest(),
        project=None,
        workspace=(
            str(raw.custom["workspace"])
            if isinstance(raw.custom.get("workspace"), str)
            else None
        ),
        text=raw.content,
    )
