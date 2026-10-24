"""The ZCode source: rollout parsing, detection, and the capability card.

What is worth pinning here: detection answers "installed on this machine"
from the home directory's existence; the transcript predicate names the
rollout streams and nothing else; and the parser files one user row and one
assistant row per model call — anchored on the request's closing message, so
a turn's prompt is imported once however many calls the turn took.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
from one_memory.imports.adapters import ScanIssueCode, ScannedEvidence, ScanOptions, ScanSkip
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.host_packages import CapabilityState, VerificationLevel
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import WrittenBy
from one_memory.imports.sources.common import SourceRecordContext
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    build_source_catalog,
)
from one_memory.imports.sources.zcode import (
    create_zcode_adapter,
    is_zcode_transcript,
    parse_zcode_record,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor

CONTEXT = SourceRecordContext(
    file_key="fixture",
    relative_path="cli/rollout/model-io-sess_1.jsonl",
    ordinal="fixture:byte:0",
)


def source_file(relative: str) -> AuthorizedSourceFile:
    return AuthorizedSourceFile(
        source_id="zcode",
        root=Path("/home/someone/.zcode"),
        path=Path("/home/someone/.zcode") / relative,
        relative_path=Path(relative),
        max_bytes=400_000_000,
        file_identity=f"identity:{relative}",
        size=10,
        modified_ns=1,
        changed_ns=1,
    )


def message(role: str, content: str) -> dict[str, JsonValue]:
    return {"role": role, "content": content}


def call_record(
    *,
    request_id: str,
    messages: list[dict[str, JsonValue]],
    response_text: str | None = None,
    query_source: str = "main_turn",
    session_id: str | None = "sess_1",
) -> dict[str, JsonValue]:
    """One rollout record, shaped as the host writes model-I/O dumps.

    `messages` is the whole request history the model saw; the response holds
    the assistant's visible text or nothing but tool calls.
    """
    return {
        "requestId": request_id,
        "turnId": request_id,
        "sessionId": session_id,
        "querySource": query_source,
        "startedAt": "2026-10-22T00:33:08.649Z",
        "completedAt": "2026-10-22T00:33:13.062Z",
        "request": {"messages": messages},
        "response": {
            "finishReason": "stop" if response_text is not None else "tool-calls",
            "text": response_text or "",
            "toolCalls": [],
        },
    }


def test_catalog_registers_zcode_as_recall_target() -> None:
    catalog = build_source_catalog(home=Path("/home/someone"))

    registration = catalog["zcode"]
    assert registration.descriptor.display_name == "ZCode"
    assert registration.candidate.path == Path("/home/someone/.zcode")
    capabilities = registration.capabilities
    assert capabilities.recall is CapabilityState.IMPLEMENTED
    assert capabilities.installer is CapabilityState.IMPLEMENTED
    assert capabilities.live_sync is CapabilityState.IMPLEMENTED
    assert capabilities.historical_import is CapabilityState.IMPLEMENTED
    assert capabilities.incremental_import is CapabilityState.IMPLEMENTED
    assert capabilities.verification is VerificationLevel.FIXTURE_ONLY


def test_transcript_predicate_names_only_rollout_streams() -> None:
    assert is_zcode_transcript(
        source_file("cli/rollout/model-io-sess_1.jsonl"),
    )
    # Config, logs, and side files live under the same root; none of them is
    # conversation data, and the parser should never see them.
    assert not is_zcode_transcript(source_file("cli/config.json"))
    assert not is_zcode_transcript(source_file("cli/log/tail.jsonl"))
    assert not is_zcode_transcript(
        source_file("cli/rollout/ledger.jsonl"),
    )


def test_prompt_record_files_the_turn_once() -> None:
    record = call_record(
        request_id="call-1",
        messages=[message("user", "为什么这个 PR 要砍掉迁移代码？")],
        response_text="因为散装形态从未发布过。",
    )

    parsed = parse_zcode_record(record, CONTEXT)
    rows = parsed.rows()  # type: ignore[union-attr]

    assert [row.role for row in rows] == ["user", "assistant"]
    assert [row.written_by for row in rows] == [WrittenBy.PERSON, WrittenBy.AGENT]
    assert [row.message_id for row in rows] == ["call-1:user", "call-1:assistant"]
    assert [row.verbatim for row in rows] == [
        "为什么这个 PR 要砍掉迁移代码？",
        "因为散装形态从未发布过。",
    ]
    assert rows[0].created_at == datetime(2026, 10, 22, 0, 33, 8, 649000, tzinfo=UTC)
    assert rows[1].created_at == datetime(2026, 10, 22, 0, 33, 13, 62000, tzinfo=UTC)
    assert [row.record_type for row in rows] == ["model_io.user", "model_io.assistant"]
    assert {row.conversation_id for row in rows} == {"sess_1"}


def test_tool_loop_records_do_not_refile_the_prompt() -> None:
    # A turn's later calls repeat the whole history and end on tool results;
    # their responses only call tools. Neither row may be filed again.
    record = call_record(
        request_id="call-2",
        messages=[
            message("user", "帮我看看这个文件"),
            message("assistant", "我来读取它。"),
            message("tool", "=> 1"),
        ],
        response_text=None,
    )

    skip = parse_zcode_record(record, CONTEXT)

    assert skip.reason_code is ScanIssueCode.TOOL_CONTENT_EXCLUDED
    assert skip.conversation_id == "sess_1"
    assert not skip.report_issue


def test_compaction_records_are_not_conversation() -> None:
    record = call_record(
        request_id="call-3",
        messages=[message("user", "继续")],
        response_text=None,
        query_source="compact",
    )

    skip = parse_zcode_record(record, CONTEXT)

    assert skip.reason_code is ScanIssueCode.INTERNAL_REASONING_EXCLUDED
    assert skip.conversation_id == "sess_1"


def test_unrecognized_records_are_named_not_swallowed() -> None:
    # A renamed field or a new record kind must read as "unknown", not as an
    # ordinary exclusion over a green sync: the skip a reviewer can act on is
    # the one that says the format changed.
    no_query_source = call_record(
        request_id="call-7",
        messages=[message("user", "没有来源标记的记录")],
        response_text=None,
    )
    del no_query_source["querySource"]
    renamed_kind = dict(
        call_record(
            request_id="call-8",
            messages=[message("user", "未来新增的记录类型")],
            response_text=None,
        ),
        querySource="summary",
    )
    bookkeeping = {"requestId": "call-9", "sessionId": "sess_1"}

    for record in (no_query_source, renamed_kind, bookkeeping):
        skip = parse_zcode_record(record, CONTEXT)
        assert skip.reason_code is ScanIssueCode.UNKNOWN_RECORD
        assert skip.conversation_id == "sess_1"


def test_injected_prompt_keeps_only_the_persons_words() -> None:
    # The recall hook and the host both wrap the prompt; the user row is what
    # the person typed, and an input that was pure injection is not a turn.
    wrapped = (
        "<memhub_memory_context>\nRecalled memory the hook injected.\n"
        "</memhub_memory_context>\n\n"
        "<system-reminder>\nAvailable agent types.\n</system-reminder>\n\n"
        "继续历史 parser 的实现"
    )
    record = call_record(
        request_id="call-4",
        messages=[message("user", wrapped)],
        response_text=None,
    )

    parsed = parse_zcode_record(record, CONTEXT)
    rows = parsed.rows()  # type: ignore[union-attr]

    assert [row.role for row in rows] == ["user"]
    assert rows[0].verbatim == "继续历史 parser 的实现"

    pure_injection = call_record(
        request_id="call-5",
        messages=[message("user", "<system-reminder>\nHost context only.\n</system-reminder>")],
        response_text=None,
    )
    skip = parse_zcode_record(pure_injection, CONTEXT)
    assert skip.reason_code is ScanIssueCode.NO_TEXT
    assert skip.conversation_id == "sess_1"


def test_conversation_id_falls_back_to_the_filename() -> None:
    record = call_record(
        request_id="call-6",
        messages=[message("user", "一次没有 sessionId 的调用")],
        response_text="回答",
        session_id=None,
    )

    parsed = parse_zcode_record(record, CONTEXT)

    assert parsed.conversation_id == "model-io-sess_1"  # type: ignore[union-attr]


def test_adapter_scan_files_rollout_records_once(tmp_path: Path) -> None:
    root = tmp_path / ".zcode"
    rollout = root / "cli" / "rollout"
    rollout.mkdir(parents=True)
    session = rollout / "model-io-sess_1.jsonl"
    records = [
        call_record(
            request_id="call-1",
            messages=[message("user", "第一轮问题")],
            response_text="第一轮回答",
        ),
        call_record(
            request_id="call-2",
            messages=[message("user", "第一轮问题"), message("tool", "=> 1")],
            response_text=None,
        ),
        call_record(
            request_id="call-3",
            messages=[message("user", "第二轮问题")],
            response_text=None,
            query_source="compact",
        ),
        call_record(
            request_id="call-4",
            messages=[message("user", "第二轮问题")],
            response_text="第二轮回答",
        ),
    ]
    session.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    # A sibling file that is not a rollout stream must never be read.
    (root / "cli" / "config.json").write_text("{}", encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="zcode", root=root, user_confirmed=True, home=tmp_path
    )
    adapter = create_zcode_adapter(grant)

    async def scan(options: ScanOptions):
        return [event async for event in adapter.scan(options)]

    events = asyncio.run(scan(ScanOptions(cursors={})))
    evidence = [event.evidence for event in events if isinstance(event, ScannedEvidence)]
    skips = [event for event in events if isinstance(event, ScanSkip)]

    assert [item.message_id for item in evidence] == [
        "call-1:user",
        "call-1:assistant",
        "call-4:user",
        "call-4:assistant",
    ]
    assert [item.verbatim for item in evidence] == [
        "第一轮问题",
        "第一轮回答",
        "第二轮问题",
        "第二轮回答",
    ]
    assert {item.conversation_id for item in evidence} == {"sess_1"}
    assert [item.reason_code for item in skips] == [
        ScanIssueCode.TOOL_CONTENT_EXCLUDED,
        ScanIssueCode.INTERNAL_REASONING_EXCLUDED,
    ]
    # Re-reading from the committed cursors files nothing twice.
    cursors = {}
    for event in events:
        cursor = getattr(event, "cursor", None)
        if cursor is not None and cursor.read_to_end:
            cursors[cursor.file_key] = cursor
    resumed = asyncio.run(scan(ScanOptions(cursors=cursors)))
    assert not any(isinstance(event, (ScannedEvidence, ScanSkip)) for event in resumed)


def test_a_compaction_rewrite_rescans_without_duplicates(tmp_path):
    """Compaction rewrites the file, dropping old records and re-placing the
    rest; a later import re-reads what remains and must not file it twice."""
    home = tmp_path / "home"
    rollout = home / ".zcode" / "cli" / "rollout"
    rollout.mkdir(parents=True)
    session = rollout / "model-io-sess_1.jsonl"
    catalog = build_source_catalog(home=home, environment={})
    grants = SourceGrantStore(tmp_path / "core.sqlite3", home=home)
    grants.grant(
        user_id="local",
        source_id="zcode",
        root=home / ".zcode",
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    jobs = ImportJobStore(tmp_path / "core.sqlite3")
    evidence = EvidenceStore(tmp_path / "core.sqlite3")

    def run_import() -> None:
        async def scenario() -> ImportStatus:
            supervisor = ImportSupervisor(
                adapters={
                    "zcode": catalog["zcode"].create_adapter(
                        grants.authorized_root(user_id="local", source_id="zcode")
                    )
                },
                evidence_store=evidence,
                job_store=jobs,
            )
            job = supervisor.create_job("zcode")
            supervisor.enqueue(job.job_id)
            return (await supervisor.wait(job.job_id)).status

        assert asyncio.run(scenario()) is ImportStatus.COMPLETED

    def stored() -> list[tuple[str, str]]:
        with sqlite3.connect(tmp_path / "core.sqlite3") as connection:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT message_id, verbatim FROM evidence ORDER BY evidence_id"
                )
            ]

    def write_records(records: list[dict[str, JsonValue]]) -> None:
        session.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    write_records(
        [
            call_record(
                request_id="call-1",
                messages=[message("user", "被压缩丢弃的一轮")],
                response_text="被丢弃的回答",
            ),
            call_record(
                request_id="call-2",
                messages=[message("user", "保留下来的一轮")],
                response_text="保留下来的回答",
            ),
        ]
    )
    run_import()
    # Sorted, not insertion-ordered: the write pipeline lands rows in its own
    # order, and what matters here is exactly which rows exist.
    assert sorted(row[0] for row in stored()) == sorted(
        [
            "call-1:user",
            "call-1:assistant",
            "call-2:user",
            "call-2:assistant",
        ]
    )

    # The rewrite drops call-1, keeps call-2 byte-similar but at a new offset,
    # and appends a fresh turn — exactly what a compacted rollout looks like.
    write_records(
        [
            call_record(
                request_id="call-2",
                messages=[message("user", "保留下来的一轮")],
                response_text="保留下来的回答",
            ),
            call_record(
                request_id="call-3",
                messages=[message("user", "压缩后的新一轮")],
                response_text="新一轮的回答",
            ),
        ]
    )
    run_import()
    assert sorted(row[0] for row in stored()) == sorted(
        [
            "call-1:user",
            "call-1:assistant",
            "call-2:user",
            "call-2:assistant",
            "call-3:user",
            "call-3:assistant",
        ]
    )
