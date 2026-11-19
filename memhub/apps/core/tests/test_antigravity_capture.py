"""Google Antigravity: visible steps only, from the host's own brain tree.

Antigravity is a file-backed source. `api.file_polled` reports a source as
file-backed when its catalog entry declares `live_sync`, and such a source is
deliberately not staged from a hook (`ebf16b939`), so there is no hook lane to
test here — the parser and the catalog entry are the whole surface.
"""

import json

import pytest

from one_memory.imports.adapters import ScanIssueCode
from one_memory.imports.models import WrittenBy
from one_memory.imports.sources.antigravity_source import (
    APP_DATA_DIRS,
    parse_antigravity_record,
    visible_text,
)
from one_memory.imports.sources.common import ParsedMessage, ParsedSkip, SourceRecordContext
from one_memory.imports.sources.registry import build_source_catalog

CONVERSATION = "0f56e1a2-1111-2222-3333-444444444444"
RELATIVE = f"{CONVERSATION}/.system_generated/logs/transcript.jsonl"


def _context(relative: str = RELATIVE, ordinal: str = "0") -> SourceRecordContext:
    return SourceRecordContext(file_key="scan", relative_path=relative, ordinal=ordinal)


def _step(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {"step_index": 0, "status": "DONE"}
    return {**base, **fields}


def test_catalog_registers_antigravity_as_file_backed_without_a_recall_installer(
    tmp_path,
) -> None:
    (tmp_path / ".gemini" / "antigravity-ide" / "brain").mkdir(parents=True)
    registration = build_source_catalog(home=tmp_path, environment={})["antigravity"]

    assert registration.descriptor.source_id == "antigravity"
    assert registration.descriptor.display_name == "Google Antigravity"
    assert registration.candidate.path == tmp_path / ".gemini" / "antigravity-ide" / "brain"
    # The poller reads the files; that is what makes the hook lane redundant.
    assert registration.capabilities.live_sync.value == "implemented"
    assert registration.capabilities.recall.value == "not_implemented"
    assert registration.capabilities.installer.value == "not_implemented"
    assert registration.capabilities.verification.value == "fixture_only"


def test_the_declared_first_variant_wins_over_a_newer_leftover(tmp_path) -> None:
    for name in APP_DATA_DIRS:
        (tmp_path / ".gemini" / name / "brain").mkdir(parents=True)
    registration = build_source_catalog(home=tmp_path, environment={})["antigravity"]
    assert registration.candidate.path == tmp_path / ".gemini" / APP_DATA_DIRS[0] / "brain"


def test_user_input_becomes_a_person_turn_without_the_envelope() -> None:
    parsed = parse_antigravity_record(
        _step(type="USER_INPUT", content="<user_query>请用深绿色</user_query>"),
        _context(),
    )
    assert isinstance(parsed, ParsedMessage)
    assert parsed.conversation_id == CONVERSATION
    assert parsed.role == "user"
    assert parsed.written_by == WrittenBy.PERSON
    assert parsed.verbatim == "请用深绿色"
    assert parsed.message_id == "step-0"


def test_planner_response_keeps_the_answer_and_drops_the_reasoning() -> None:
    parsed = parse_antigravity_record(
        _step(
            step_index=3,
            type="PLANNER_RESPONSE",
            content="已改成深绿色。",
            thinking="private chain of thought",
            tool_calls=[{"name": "view_file", "args": {}}],
        ),
        _context(),
    )
    assert isinstance(parsed, ParsedMessage)
    assert parsed.role == "assistant"
    assert parsed.written_by == WrittenBy.AGENT
    assert parsed.verbatim == "已改成深绿色。"
    assert "private chain of thought" not in parsed.verbatim


def test_tool_output_and_bookkeeping_stay_out() -> None:
    for kind in ("GENERIC", "SYSTEM_MESSAGE", "CONVERSATION_HISTORY", "CHECKPOINT"):
        parsed = parse_antigravity_record(
            _step(type=kind, content="tool output that is not conversation"),
            _context(),
        )
        assert isinstance(parsed, ParsedSkip), kind
        assert parsed.reason_code == ScanIssueCode.UNKNOWN_RECORD


def test_unfinished_steps_are_left_for_the_next_scan() -> None:
    parsed = parse_antigravity_record(
        _step(type="USER_INPUT", status="RUNNING", content="<user_query>still typing</user_query>"),
        _context(),
    )
    assert isinstance(parsed, ParsedSkip)


def test_clipped_content_is_not_stored_as_half_a_sentence() -> None:
    parsed = parse_antigravity_record(
        _step(type="PLANNER_RESPONSE", content="半个句子", truncated_fields=["content"]),
        _context(),
    )
    assert isinstance(parsed, ParsedSkip)
    assert parsed.reason_code == ScanIssueCode.NO_TEXT


def test_the_untruncated_sibling_file_never_double_imports_a_step() -> None:
    """transcript_full.jsonl holds the same steps; only transcript.jsonl is read."""
    parsed = parse_antigravity_record(
        _step(type="USER_INPUT", content="hello"),
        _context(f"{CONVERSATION}/.system_generated/logs/transcript_full.jsonl"),
    )
    assert isinstance(parsed, ParsedSkip)
    assert parsed.reason_code == ScanIssueCode.UNKNOWN_RECORD


def test_a_jsonl_outside_the_brain_layout_is_not_antigravity() -> None:
    for relative in ("notes/transcript.jsonl", f"{CONVERSATION}/transcript.jsonl"):
        parsed = parse_antigravity_record(_step(type="USER_INPUT", content="hello"), _context(relative))
        assert isinstance(parsed, ParsedSkip), relative


def test_injected_memory_preface_is_not_the_persons_words() -> None:
    parsed = parse_antigravity_record(
        _step(type="PLANNER_RESPONSE", content="om-derived: injected memory\n最终答复"),
        _context(),
    )
    assert isinstance(parsed, ParsedMessage)
    assert parsed.verbatim == "最终答复"


@pytest.mark.parametrize(
    "content",
    ("", "   ", "<user_query></user_query>", "om-derived: only an injected preface"),
)
def test_content_with_nothing_visible_is_skipped(content: str) -> None:
    parsed = parse_antigravity_record(_step(type="USER_INPUT", content=content), _context())
    assert isinstance(parsed, ParsedSkip)
    assert parsed.reason_code == ScanIssueCode.NO_TEXT


def test_missing_step_index_falls_back_to_the_record_ordinal() -> None:
    record = {"type": "USER_INPUT", "status": "DONE", "content": "hello"}
    parsed = parse_antigravity_record(record, _context(ordinal="7"))
    assert isinstance(parsed, ParsedMessage)
    assert parsed.message_id == "line-7"


def test_timestamp_is_parsed_when_the_host_wrote_one() -> None:
    parsed = parse_antigravity_record(
        _step(type="USER_INPUT", content="hello", created_at="2026-11-05T06:00:00Z"),
        _context(),
    )
    assert isinstance(parsed, ParsedMessage)
    assert parsed.created_at is not None
    assert parsed.created_at.year == 2026


def test_visible_text_is_shared_by_both_readers() -> None:
    assert visible_text("<user_query>hi</user_query>") == "hi"
    assert visible_text("a\nom-derived: injected\nb") == "a\nb"
    assert visible_text("<user_query>  </user_query>") is None
