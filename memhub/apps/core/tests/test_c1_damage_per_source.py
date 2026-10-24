"""Damage and unknown schema, per source: a bad record must not sink the import.

The matrix asks for damaged/unsupported-schema behaviour per source. The property
that matters is uniform: one broken record is reported as a visible failure, the
job still completes, whatever was already readable is kept, and a later repair
imports the fixed record without duplicating the good ones.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.sources.registry import RootCandidateKind, build_source_catalog
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor

PAUSED = {"openclaw"}


class Source:
    def __init__(self, tmp_path: Path, source: str) -> None:
        self.source = source
        self.home = tmp_path / "home"
        (self.home / ".cursor").mkdir(parents=True, exist_ok=True)
        self.catalog = build_source_catalog(home=self.home, environment={})
        self.root = self.catalog[source].candidate.path
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = tmp_path / "core.sqlite3"
        self.jobs = ImportJobStore(self.database)
        self.evidence = EvidenceStore(self.database)
        self.grants = SourceGrantStore(self.database, home=self.home)
        self.grants.grant(
            user_id="local",
            source_id=source,
            root=self.root,
            candidate_kind=RootCandidateKind.DEFAULT,
        )

    def run(self):
        async def scenario():
            supervisor = ImportSupervisor(
                adapters={
                    self.source: self.catalog[self.source].create_adapter(
                        self.grants.authorized_root(user_id="local", source_id=self.source)
                    )
                },
                evidence_store=self.evidence,
                job_store=self.jobs,
            )
            job = supervisor.create_job(self.source)
            supervisor.enqueue(job.job_id)
            return await supervisor.wait(job.job_id)

        return asyncio.run(scenario())


def _jsonl_sources() -> list[str]:
    catalog = build_source_catalog(home=Path.home(), environment={})
    return sorted(source for source in catalog if source not in PAUSED | {"hermes", "opencode"})


def _good_record(source: str, index: int) -> dict:
    text = f"{source}-ok-{index}"
    if source == "codex":
        return {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        }
    if source == "cursor":
        return {"role": "user", "message": {"content": [{"type": "text", "text": text}]}}
    if source == "pi":
        return {
            "type": "message",
            "id": f"pi-{index}",
            "timestamp": "2026-09-28T10:00:00+08:00",
            "message": {"role": "user", "content": text},
        }
    if source == "deepseek-harness":
        return {
            "type": "user/message",
            "seq": index,
            "data": {
                "id": f"dsh-{index}",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": text}],
            },
        }
    if source == "zcode":
        return {
            "requestId": f"{source}-{index}",
            "sessionId": f"{source}-session",
            "querySource": "main_turn",
            "startedAt": "2026-09-28T10:00:00+08:00",
            "completedAt": "2026-09-28T10:00:01+08:00",
            "request": {"messages": [{"role": "user", "content": text}]},
            "response": {"finishReason": "tool-calls", "text": "", "toolCalls": []},
        }
    return {
        "type": "user",
        "sessionId": f"{source}-session",
        "timestamp": "2026-09-28T10:00:00+08:00",
        "message": {"content": text},
    }


def _jsonl_path(root: Path, source: str, name: str) -> Path:
    if source == "cursor":
        return root / "projects/acme/agent-transcripts" / name / "matrix.jsonl"
    if source == "qwen-code":
        return root / "chats" / f"{'0' * 32}.jsonl"
    if source == "deepseek-harness":
        return root / name / "session.jsonl"
    if source == "zcode":
        return root / "cli" / "rollout" / f"model-io-{name}.jsonl"
    return root / "project" / f"rollout-{name}.jsonl"


@pytest.mark.parametrize("source", _jsonl_sources())
def test_a_broken_record_is_reported_and_the_rest_survives(tmp_path, source):
    fixture = Source(tmp_path, source)
    path = _jsonl_path(fixture.root, source, "damage")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(_good_record(source, 0)) + "\n")
        stream.write('{"broken":\n')  # truncated JSON
        stream.write(json.dumps(_good_record(source, 1)) + "\n")

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED, "one bad record must not fail the job"
    assert result.errors >= 1, "the damage must be visible, not silently dropped"
    assert fixture.evidence.count() >= 1, "the readable record must still land"


@pytest.mark.parametrize("source", _jsonl_sources())
def test_an_unsupported_schema_never_imports_person_words(tmp_path, source):
    """A record shaped like another product's data must not become a person's words."""
    fixture = Source(tmp_path, source)
    path = _jsonl_path(fixture.root, source, "unknown")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"unknown_kind": "whatever", "payload": "SECRET"}) + "\n")

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    stored = " ".join(row.verbatim for row in fixture.evidence.list_extractable(limit=50))
    assert "SECRET" not in stored


def test_a_damaged_sqlite_source_is_reported_not_swallowed(tmp_path):
    """Unknown schema in a SQLite source returns a machine code, not a silent empty import."""
    fixture = Source(tmp_path, "opencode")
    connection = sqlite3.connect(fixture.root / "opencode.db")
    try:
        connection.execute("CREATE TABLE unrelated (id TEXT, note TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('1', 'SECRET')")
        connection.commit()
    finally:
        connection.close()

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    assert fixture.evidence.count() == 0
    stored = " ".join(row.verbatim for row in fixture.evidence.list_extractable(limit=50))
    assert "SECRET" not in stored
