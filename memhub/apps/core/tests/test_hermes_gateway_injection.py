"""Hermes gateway text in `user` rows: notices are skipped, relayed messages unwrapped.

Every fixture row is a real Hermes `state.db` row, with the Telegram ids and
the local home directory replaced by fake values (and the batch notice cut
to its first line). They all wear the `user` role, so the import has to
decide on the text.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue

from one_memory.conversation_processing import turn_batches
from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanEvent,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    ScanSkip,
)
from one_memory.imports.sources.common import (
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
)
from one_memory.imports.sources.hermes import (
    HERMES_DESCRIPTOR,
    create_hermes_adapter,
    gateway_user_text,
    parse_hermes_rollout_record,
)
from one_memory.imports.storage import EvidenceStore

_ROWS = {
    row["id"]: row
    for row in (
        json.loads(line)
        for line in (Path(__file__).parent / "fixtures" / "hermes_gateway_rows.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
}

_DROPPED = {
    131: ScanIssueCode.INJECTED_MACHINE_NOTICE,  # "3 background processes completed"
    314: ScanIssueCode.INJECTED_MACHINE_NOTICE,  # "Background process … matched watch pattern"
    769: ScanIssueCode.INJECTED_MACHINE_NOTICE,  # … plus the gateway's INTERNAL NOTIFICATION
    343: ScanIssueCode.VOICE_PLACEHOLDER,
    197: ScanIssueCode.PROBE,
}

_UNWRAPPED = {
    548: "对比结果如何",
    637: "3.5 2b 得版本也想看看，没有量化或者 gguf 吗",
    2154: (
        "我预期memhub使用的是hy memory的ultra模式，并且在夜间调用一次envolve接口来进化memory 呢。\n"
        "你说memhub的chat走的是什么我有点懵。\n\n"
        "我记得memhub写入的时候，不是直接调用sdk client的add么？\n\n"
        "应该是没有设置scene才对。可以检查一下"
    ),
}


@pytest.mark.parametrize("row_id", sorted(_DROPPED))
def test_a_machine_turn_in_the_users_role_is_skipped_with_its_reason(row_id: int) -> None:
    assert gateway_user_text(_ROWS[row_id]["content"]) == ("", _DROPPED[row_id])


def test_the_gateway_note_drops_a_turn_wherever_it_sits() -> None:
    note = _ROWS[769]["content"].rsplit("\n\n", 1)[1]
    assert note.startswith("[INTERNAL NOTIFICATION")

    assert gateway_user_text(f"anything before it\n\n{note}")[1] is (
        ScanIssueCode.INJECTED_MACHINE_NOTICE
    )


@pytest.mark.parametrize("row_id", sorted(_UNWRAPPED))
def test_a_relayed_message_keeps_exactly_what_the_person_wrote(row_id: int) -> None:
    assert gateway_user_text(_ROWS[row_id]["content"]) == (_UNWRAPPED[row_id], None)


def test_the_interrupted_response_label_is_unwrapped() -> None:
    text = "[Context from the interrupted assistant response]\n继续刚才的对比"

    assert gateway_user_text(text) == ("继续刚才的对比", None)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("MEDIA:/Users/example/.hermes/cache/images/a.jpg", ScanIssueCode.NO_TEXT),
        ("Reply with exactly: PONG please, and then explain why", None),
        ("平铺", None),
        ("  look at [IMPORTANT: Background process] in the log  ", None),
    ],
)
def test_ordinary_text_is_left_as_it_was(text: str, reason: ScanIssueCode | None) -> None:
    cleaned, skipped = gateway_user_text(text)

    assert skipped is reason
    assert cleaned == ("" if reason else text.strip())


def _state_db(root: Path, rows: list[tuple[int, str, str, str]]) -> None:
    root.mkdir()
    connection = sqlite3.connect(root / "state.db")
    connection.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
        " content TEXT, timestamp REAL, tool_name TEXT, active INTEGER)"
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, 1000, ?, 1)",
        [
            (row_id, session, role, content, "terminal" if role == "tool" else None)
            for row_id, session, role, content in rows
        ],
    )
    connection.commit()
    connection.close()


def _scan(root: Path, home: Path) -> list[ScanEvent]:
    adapter = create_hermes_adapter(
        AuthorizedSourceRoot.grant(
            source_id=HERMES_DESCRIPTOR.source_id,
            root=root,
            user_confirmed=True,
            home=home,
        )
    )

    async def collect() -> list[ScanEvent]:
        return [event async for event in adapter.scan(ScanOptions(cursors={}))]

    return asyncio.run(collect())


def test_state_db_import_writes_the_cleaned_turn_and_counts_the_skipped_ones(
    tmp_path: Path,
) -> None:
    rows = [
        (row_id, "session-h", "user", _ROWS[row_id]["content"])
        for row_id in [*_DROPPED, *_UNWRAPPED]
    ]
    _state_db(tmp_path / "hermes", rows)
    events = _scan(tmp_path / "hermes", tmp_path)
    evidence = [event.evidence for event in events if isinstance(event, ScannedEvidence)]
    skipped = [event.reason_code for event in events if isinstance(event, ScanSkip)]

    assert [item.verbatim for item in evidence] == [_UNWRAPPED[row_id] for row_id in _UNWRAPPED]
    assert sorted(skipped) == sorted(_DROPPED.values())
    assert "chat_id" not in repr(evidence)


def test_assistant_and_tool_rows_with_the_same_text_are_not_touched(tmp_path: Path) -> None:
    """Only the person's role is cleaned. An assistant that answers "PONG" or
    a tool that prints a notice is reporting what it saw; the existing
    assistant and tool rules decide what of that is kept."""
    probe = _ROWS[197]["content"]
    relayed = _ROWS[548]["content"]
    _state_db(
        tmp_path / "hermes",
        [
            (1, "session-h", "assistant", probe),
            (2, "session-h", "assistant", relayed),
            (3, "session-h", "tool", probe),
        ],
    )

    evidence = [
        event.evidence
        for event in _scan(tmp_path / "hermes", tmp_path)
        if isinstance(event, ScannedEvidence)
    ]

    assert [item.role for item in evidence] == ["assistant", "assistant", "tool"]
    assert evidence[0].verbatim == probe
    assert evidence[1].verbatim == relayed
    assert probe in evidence[2].verbatim


def test_a_conversation_left_without_its_user_turn_writes_no_batch(tmp_path: Path) -> None:
    """A probe session is the probe and the reply. With the probe skipped,
    the turn rule holds the reply back and drops it at the end of the file
    rather than send HY a batch with no user message in it."""
    _state_db(
        tmp_path / "hermes",
        [(1, "probe", "user", _ROWS[197]["content"]), (2, "probe", "assistant", "PONG")],
    )
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    records = [
        store.put(event.evidence).record
        for event in _scan(tmp_path / "hermes", tmp_path)
        if isinstance(event, ScannedEvidence)
    ]

    assert [record.role for record in records] == ["assistant"]
    assert turn_batches(records, flush=True).batches == ()


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_session_transcripts_follow_the_same_rule(role: str) -> None:
    context = SourceRecordContext(
        file_key="hermes",
        relative_path="sessions/session.jsonl",
        ordinal="1",
    )

    def parse(text: str) -> ParsedMessage | ParsedSkip:
        record: dict[str, JsonValue] = {
            "type": "message",
            "role": role,
            "content": text,
            "sessionId": "s",
        }
        return parse_hermes_rollout_record(record, context)

    notice = parse(_ROWS[314]["content"])
    relayed = parse(_ROWS[548]["content"])

    if role == "user":
        assert notice == ParsedSkip(
            reason_code=ScanIssueCode.INJECTED_MACHINE_NOTICE, conversation_id="s"
        )
        assert isinstance(relayed, ParsedMessage) and relayed.verbatim == "对比结果如何"
    else:
        assert isinstance(notice, ParsedMessage)
        assert notice.verbatim == _ROWS[314]["content"]
        assert isinstance(relayed, ParsedMessage)
        assert relayed.verbatim == _ROWS[548]["content"]
