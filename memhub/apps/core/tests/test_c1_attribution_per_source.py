"""Attribution per source, asserted on stored Evidence, not just on parsed events.

The parser tests already assert `written_by` / `created_at` / conversation id for
each source. What this adds is the part downstream actually consumes: after the
import commits, the stored row keeps the same author, the same missing-time
semantics, and a stable per-source conversation id — and machine-written turns
never come back as a person's words.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import WrittenBy
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


def _person_and_agent(source: str) -> list[dict]:
    """One person turn and one assistant turn in this source's own shape."""
    if source == "codex":
        return [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "person words"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "agent words"}],
                },
            },
        ]
    if source == "cursor":
        return [
            {"role": "user", "message": {"content": [{"type": "text", "text": "person words"}]}},
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "agent words"}]},
            },
        ]
    if source == "pi":
        return [
            {"type": "session", "id": "s", "cwd": "/private/project"},
            {"type": "message", "id": "a", "message": {"role": "user", "content": "person words"}},
            {
                "type": "message",
                "id": "b",
                "message": {"role": "assistant", "content": "agent words"},
            },
        ]
    if source == "deepseek-harness":
        return [
            {"type": "session", "id": "s", "cwd": "/private/project"},
            {
                "type": "user/message",
                "seq": 1,
                "data": {
                    "id": "a",
                    "role": "user",
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": "person words"}],
                },
            },
            {
                "type": "assistant/message",
                "seq": 2,
                "data": {
                    "message": {
                        "id": "b",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "agent words"}],
                    }
                },
            },
        ]
    if source == "zcode":
        # One rollout call: the request closes on the prompt, the response
        # carries the answer — both turns of the exchange in one record.
        return [
            {
                "requestId": f"{source}-1",
                "sessionId": f"{source}-session",
                "querySource": "main_turn",
                "startedAt": "2026-09-28T10:00:00+08:00",
                "completedAt": "2026-09-28T10:00:01+08:00",
                "request": {"messages": [{"role": "user", "content": "person words"}]},
                "response": {"finishReason": "stop", "text": "agent words", "toolCalls": []},
            }
        ]
    return [
        {
            "type": "user",
            "sessionId": f"{source}-session",
            "timestamp": "2026-09-28T10:00:00+08:00",
            "message": {"content": "person words"},
        },
        {
            "type": "assistant",
            "sessionId": f"{source}-session",
            "timestamp": "2026-09-28T10:00:01+08:00",
            "message": {"content": "agent words"},
        },
    ]


def _jsonl_path(root: Path, source: str) -> Path:
    if source == "cursor":
        return root / "projects/acme/agent-transcripts/attribution/matrix.jsonl"
    if source == "qwen-code":
        return root / "chats" / f"{'0' * 32}.jsonl"
    if source == "deepseek-harness":
        return root / "attribution" / "session.jsonl"
    if source == "zcode":
        return root / "cli" / "rollout" / "model-io-attribution.jsonl"
    return root / "project" / "rollout-attribution.jsonl"


def _jsonl_sources() -> list[str]:
    catalog = build_source_catalog(home=Path.home(), environment={})
    return sorted(source for source in catalog if source not in PAUSED | {"hermes", "opencode"})


def _stored_rows(fixture: Source) -> list[dict]:
    """Read stored Evidence directly.

    `list_extractable` returns only rows still pending extraction, so counting
    with it under-reports and would quietly weaken every assertion below.
    """
    with sqlite3.connect(fixture.database) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT source_id, conversation_id, role, written_by, created_at, verbatim "
                "FROM evidence ORDER BY evidence_id"
            )
        ]


@pytest.mark.parametrize("source", _jsonl_sources())
def test_stored_rows_keep_author_and_conversation_identity(tmp_path, source):
    fixture = Source(tmp_path, source)
    path = _jsonl_path(fixture.root, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in _person_and_agent(source):
            stream.write(json.dumps(record) + "\n")

    result = fixture.run()
    assert result.status is ImportStatus.COMPLETED
    rows = _stored_rows(fixture)
    assert len(rows) == 2, f"{source} must store exactly the person and agent turn"

    by_words = {row["verbatim"]: row for row in rows}
    assert by_words["person words"]["written_by"] == WrittenBy.PERSON.value
    assert by_words["agent words"]["written_by"] == WrittenBy.AGENT.value
    # One source's conversation identity, stable across both rows.
    assert len({row["conversation_id"] for row in rows}) == 1
    assert all(row["source_id"] == source for row in rows)


@pytest.mark.parametrize("source", _jsonl_sources())
def test_a_machine_written_user_turn_is_never_attributed_to_a_person(tmp_path, source):
    """Scheduled/environment turns wear the user role; the role alone must not decide."""
    fixture = Source(tmp_path, source)
    path = _jsonl_path(fixture.root, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _person_and_agent(source)
    if source == "deepseek-harness":
        records.append(
            {
                "type": "user/message",
                "seq": 3,
                "data": {
                    "id": "machine",
                    "role": "user",
                    "source": {"kind": "environment"},
                    "content": [{"type": "text", "text": "MACHINE_NOT_A_PERSON"}],
                },
            }
        )
    elif source == "pi":
        # Pi keeps automation in its own directory, which is skipped wholesale.
        records = records
    else:
        # Each source marks a machine-written turn its own way; the point of this
        # test is that the marker is honoured, so it has to be the real one.
        marker: dict = {"providerData": {"isSubAgent": True}}
        if source == "claude-code":
            marker = {"isSidechain": True}
        elif source == "qwenwork":
            marker = {"origin": {"kind": "agent"}}
        elif source == "qwen-code":
            # qwen-code decides authorship from `provenance`, not from a flag:
            # anything that is not `real_user` is not the person's words.
            marker = {"provenance": "tool_result"}
        records.append(
            {
                "type": "user",
                "sessionId": f"{source}-session",
                "timestamp": "2026-09-28T10:00:02+08:00",
                "message": {"content": "MACHINE_NOT_A_PERSON"},
                **marker,
            }
        )
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")

    fixture.run()
    rows = _stored_rows(fixture)
    machine = [row for row in rows if row["verbatim"] == "MACHINE_NOT_A_PERSON"]
    for row in machine:
        assert row["written_by"] != WrittenBy.PERSON.value, (
            f"{source} attributed a machine turn to a person"
        )
    if source == "qwen-code":
        # This source excludes it outright, which is the stronger correct answer.
        assert not machine, "qwen-code must exclude a non-real_user turn entirely"


def test_missing_time_stays_null_after_commit(tmp_path):
    """Unknown time must survive to the stored row, not become the import time."""
    fixture = Source(tmp_path, "codex")
    path = _jsonl_path(fixture.root, "codex")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        # No timestamp anywhere: the record simply carries none.
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "no time given"}],
                    },
                }
            )
            + "\n"
        )

    fixture.run()
    rows = _stored_rows(fixture)
    assert len(rows) == 1
    assert rows[0]["created_at"] is None, "an unknown message time must stay unknown"


def test_sqlite_sources_mark_unknown_user_rather_than_person(tmp_path):
    """Hermes/OpenCode have no structured author evidence, so user means unknown."""
    fixture = Source(tmp_path, "opencode")
    connection = sqlite3.connect(fixture.root / "opencode.db")
    try:
        connection.executescript(
            """
            CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
            CREATE TABLE message (
                id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT
            );
            INSERT INTO session VALUES ('o', '/private/project');
            INSERT INTO message VALUES ('m', 'o', 1000, '{"role":"user"}');
            INSERT INTO part VALUES ('p', 'm', 1000, '{"type":"text","text":"opencode words"}');
            """
        )
        connection.commit()
    finally:
        connection.close()

    fixture.run()
    rows = _stored_rows(fixture)
    assert len(rows) == 1
    assert rows[0]["written_by"] == WrittenBy.UNKNOWN.value
