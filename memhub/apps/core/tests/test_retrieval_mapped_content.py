import hashlib
import json
import socket
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from one_memory.imports.models import EvidenceContentKind, EvidenceRecord, WrittenBy
from one_memory.input_sanitization import SanitizedCopySpan, SanitizedInput, sanitize_model_input
from one_memory.local_embedding import LocalEmbedder, TokenizedText, TokenOffset
from one_memory.retrieval import mapped_content as mapped
from one_memory.retrieval.chunking import ChunkingError
from one_memory.retrieval.mapped_content import (
    MappedContentError,
    chunk_mapped_content,
    prepare_mapped_content,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("mapped content must remain offline")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)


def record(text, **changes):
    values = dict(
        evidence_id="synthetic-evidence",
        user_id="local",
        source_id="synthetic-source",
        workspace=None,
        project=None,
        conversation_id="synthetic-conversation",
        message_id="synthetic-message",
        correlation_id=None,
        source_ordinal=None,
        content_kind=EvidenceContentKind.RAW_CONVERSATION,
        role="user",
        written_by=WrittenBy.PERSON,
        created_at=None,
        verbatim=text,
        origin_meta={},
        content_hash="outer-manifest-owned",
        revision=4,
        imported_at=datetime(2026, 9, 19, tzinfo=UTC),
        updated_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    return EvidenceRecord(**(values | changes))


class Characters:
    def __init__(self):
        self.calls = []

    def tokenize_with_offsets(self, text):
        self.calls.append(text)
        offsets = (
            TokenOffset(0, 0, True),
            *(TokenOffset(i, i + 1) for i in range(len(text))),
            TokenOffset(0, 0, True),
        )
        return TokenizedText(tuple(range(len(offsets))), offsets)


def assert_map(original, content):
    meta = content.metadata
    assert meta.source_length_cp == len(original)
    assert meta.sanitized_length_cp == len(content.actual_text)
    assert meta.sanitized_sha256 == hashlib.sha256(content.actual_text.encode()).hexdigest()
    output_end = 0
    copied = []
    for span in meta.segments:
        assert span.start_cp == output_end < span.end_cp
        output_end = span.end_cp
        if span.kind == "copy":
            assert (
                content.actual_text[span.start_cp : span.end_cp]
                == original[span.source_start_cp : span.source_end_cp]
            )
            copied.append((span.source_start_cp, span.source_end_cp))
        else:
            assert span.kind == "synthetic"
            assert span.source_start_cp is span.source_end_cp is None
    assert output_end == len(content.actual_text)
    ranges = sorted(copied + [(s.start_cp, s.end_cp) for s in meta.excluded])
    end = 0
    for start, right in ranges:
        assert start == end < right
        end = right
    assert end == len(original)


def assert_chunks(original, prepared, chunks):
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    coverage = set()
    for c in chunks:
        assert c.text == original[c.start_cp : c.end_cp]
        assert c.token_count <= 512
        assert any(
            s.kind == "copy" and s.source_start_cp <= c.start_cp < c.end_cp <= s.source_end_cp
            for s in prepared.metadata.segments
        )
        coverage.update(range(c.start_cp, c.end_cp))
    for s in prepared.metadata.segments:
        if s.kind == "copy":
            assert all(
                i in coverage
                for i in range(s.source_start_cp, s.source_end_cp)
                if not original[i].isspace()
            )


@pytest.mark.parametrize(
    "role,author", [("user", WrittenBy.PERSON), ("assistant", WrittenBy.AGENT)]
)
def test_raw_authors_keep_exact_body_and_do_not_mutate_evidence(role, author):
    original = " \t\n\u4e2d\U0001f680e\u0301 same\n same\t "
    evidence = record(original, role=role, written_by=author)
    before = evidence.model_dump()
    content = prepare_mapped_content(evidence)
    assert content.state == "ready"
    assert content.actual_text == original.strip()
    assert evidence.model_dump() == before
    assert_map(original, content)
    assert_chunks(original, content, chunk_mapped_content(content, Characters()))


@pytest.mark.parametrize(
    "change",
    [
        {"role": "tool", "written_by": WrittenBy.TOOL},
        {"role": "system"},
        {"content_kind": EvidenceContentKind.MEMORY_CONTENT},
    ],
)
def test_raw_role_qualification_rejects_without_tokenizing(change):
    with pytest.raises(MappedContentError, match="mapped_content_ineligible"):
        prepare_mapped_content(record("ordinary body", **change))


@pytest.mark.parametrize("author", list(WrittenBy))
def test_host_author_guess_does_not_change_protocol_role_qualification(author):
    content = prepare_mapped_content(record("ordinary body", role="user", written_by=author))
    assert content.state == "ready"


@pytest.mark.parametrize(
    "text,reason",
    [
        ("<user_query>one</user_query><user_query>two</user_query>", "ambiguous"),
        ("<user_info>unclosed", "ambiguous"),
        ("<rules>context</rules> unexplained trailing body", "ambiguous"),
        ("<dynamic_tool_catalog>generated</dynamic_tool_catalog>", "generated"),
        ('{"request_id":"fixture","type":"write","steps":[]}', "generated"),
        (
            "<user_query><dynamic_tool_catalog>generated</dynamic_tool_catalog></user_query>",
            "generated",
        ),
        ("<user_query><environment_context>harness</environment_context></user_query>", "hygiene"),
    ],
)
def test_ambiguous_generated_and_body_hygiene_are_not_admitted(text, reason):
    with pytest.raises(MappedContentError, match=reason):
        prepare_mapped_content(record(text))


def test_wrapper_offsets_compose_by_position_despite_repeated_text_and_unicode():
    prefix = " \t<rules>same \U0001f680</rules>\n<user_query> \n"
    body = "same \U0001f680 token=same same e\u0301"
    original = prefix + body + " \n</user_query><image_local_path>same</image_local_path> "
    prepared = prepare_mapped_content(record(original))
    assert prepared.actual_text == sanitize_model_input(body)
    assert prepared.metadata.body_start_cp == len(prefix)
    assert prepared.metadata.body_end_cp == len(prefix) + len(body)
    assert_map(original, prepared)
    tokenizer = Characters()
    chunks = chunk_mapped_content(prepared, tokenizer, target_tokens=10, overlap_tokens=3)
    assert_chunks(original, prepared, chunks)
    assert chunks[0].start_cp == len(prefix)
    assert all("<rules>" not in text and "redacted-secret" not in text for text in tokenizer.calls)


@pytest.mark.parametrize(
    "secret",
    [
        "token=private-value",
        "Authorization: Bearer abcdefghijklmnop",
        '--password "two words"',
        "sk-abcdefghijklmnopqrstuvwxyz",
        "https://alice:private-password@example.test/path",
        "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
    ],
)
def test_secrets_and_synthetic_replacements_never_reach_tokenizer(secret):
    original = "left \u4e2d\U0001f680 " + secret + " right e\u0301 /final/file"
    content = prepare_mapped_content(record(original))
    tokenizer = Characters()
    chunks = chunk_mapped_content(content, tokenizer, target_tokens=12, overlap_tokens=4)
    assert_map(original, content)
    assert_chunks(original, content, chunks)
    assert all(secret not in text and "redacted-secret" not in text for text in tokenizer.calls)
    assert any("left" in c.text for c in chunks) and any("right" in c.text for c in chunks)


@pytest.mark.parametrize(
    "text", ["", " \n\t ", "<rules>only wrapper</rules>", "token=private", " token=x \t token=y "]
)
def test_empty_wrapper_or_all_excluded_is_explicit_nonindexable(text):
    content = prepare_mapped_content(record(text))
    assert content.state == "no_indexable_content"
    assert_map(text, content)
    tokenizer = Characters()
    assert chunk_mapped_content(content, tokenizer) == ()
    assert tokenizer.calls == []


def test_metadata_is_immutable_canonical_and_never_contains_text_or_secret_hashes():
    original = "<user_query>prefix token=private-value suffix</user_query>"
    content = prepare_mapped_content(record(original))
    encoded = content.metadata.canonical_json()
    assert encoded == json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":"))
    assert content.metadata.digest == hashlib.sha256(encoded.encode()).hexdigest()
    for text in [
        "prefix",
        "suffix",
        "private-value",
        "redacted-secret",
        hashlib.sha256(b"private-value").hexdigest(),
    ]:
        assert text not in encoded
    assert content.metadata == prepare_mapped_content(record(original)).metadata
    with pytest.raises(FrozenInstanceError):
        content.metadata.source_length_cp = 1
    with pytest.raises(FrozenInstanceError):
        content.metadata.segments[0].start_cp = 1
    with pytest.raises(FrozenInstanceError):
        content.actual_text = "other"


def test_digest_changes_for_safe_text_or_coordinate_version_not_equal_length_excluded_secret():
    first = prepare_mapped_content(record("left token=secret-one right"))
    second = prepare_mapped_content(record("left token=secret-two right"))
    assert first.metadata.digest == second.metadata.digest
    assert (
        first.metadata.digest
        != prepare_mapped_content(record("LEFT token=secret-one right")).metadata.digest
    )
    assert first.metadata.digest != replace(first.metadata, map_version="future-map").digest


def test_overlapping_sanitizer_passes_and_literal_placeholder_are_distinct():
    text = "<redacted-secret> token=sk-abcdefghijklmnopqrstuvwxyz /tail"
    content = prepare_mapped_content(record(text))
    assert_map(text, content)
    chunks = chunk_mapped_content(content, Characters())
    assert chunks[0].text == "<redacted-secret> "  # Literal source text is copy, not synthetic.
    assert chunks[-1].text == " /tail"


@pytest.mark.parametrize("text", ["\u4e2d\x00\u6587", "\u4e2d\u200b\u6587", "\u4e2d\u200d\u6587"])
def test_control_characters_are_not_silently_sanitized_or_dropped(text):
    content = prepare_mapped_content(record(text))
    assert content.actual_text == text
    with pytest.raises(ChunkingError, match="uncovered_nonwhitespace"):
        chunk_mapped_content(content, LocalEmbedder())


def test_locked_tokenizer_unicode_coverage_and_prefix_budget():
    body = ("\u4e2d\u6587\U0001f680e\u0301\n" * 400) + " token=private " + ("\u5c3e\u6587\n" * 400)
    original = "<user_query>" + body + "</user_query>"
    content = prepare_mapped_content(record(original))
    tokenizer = LocalEmbedder()
    prefix = "\u68c0\u7d22\u524d\u7f00" * 30
    chunks = chunk_mapped_content(content, tokenizer, prefix=prefix)
    assert len(chunks) > 4
    assert_chunks(original, content, chunks)
    assert all(
        c.token_count == len(tokenizer.tokenize_with_offsets(prefix + c.text).token_ids)
        for c in chunks
    )


def test_more_than_8192_chunks_aborts_one_copy_span_without_returning_partial_content():
    tokenizer = Characters()
    content = prepare_mapped_content(record("x" * 9000))
    with pytest.raises(MappedContentError, match="mapped_chunk_limit_exceeded"):
        chunk_mapped_content(content, tokenizer, target_tokens=1, overlap_tokens=0)
    assert len(tokenizer.calls) <= 8194


def test_global_limit_across_copy_spans_and_exact_limit_succeeds():
    content = prepare_mapped_content(record("a token=private b"))
    with pytest.raises(MappedContentError, match="mapped_chunk_limit_exceeded"):
        chunk_mapped_content(content, Characters(), max_chunks=1)
    chunks = chunk_mapped_content(content, Characters(), max_chunks=2)
    assert [c.ordinal for c in chunks] == [0, 1]


@pytest.mark.parametrize("limit", [0, -1, True, 8193])
def test_chunk_limit_is_not_raiseable_or_coercible(limit):
    with pytest.raises(MappedContentError, match="mapped_invalid_chunk_limit"):
        chunk_mapped_content(prepare_mapped_content(record("body")), Characters(), max_chunks=limit)


def test_forged_text_and_incomplete_sanitizer_mapping_fail_closed(monkeypatch):
    prepared = prepare_mapped_content(record("body"))
    with pytest.raises(MappedContentError, match="mapped_invalid"):
        chunk_mapped_content(replace(prepared, actual_text="BODY"), Characters())
    monkeypatch.setattr(
        mapped,
        "sanitize_model_input_with_spans",
        lambda text: SanitizedInput(text, (SanitizedCopySpan(0, 2, 0, 1),), ()),
    )
    with pytest.raises(MappedContentError, match="mapped_invalid"):
        prepare_mapped_content(record("body"))


def test_exact_8192_chunks_and_shrink_retries_do_not_spend_extra_chunk_budget():
    content = prepare_mapped_content(record("x" * 8192))
    chunks = chunk_mapped_content(content, Characters(), target_tokens=1, overlap_tokens=0)
    assert len(chunks) == 8192 and chunks[-1].ordinal == 8191
    content = prepare_mapped_content(record("x" * 10))
    chunks = chunk_mapped_content(
        content,
        Characters(),
        target_tokens=8,
        overlap_tokens=0,
        max_tokens=8,
        prefix="PP",
        max_chunks=3,
    )
    assert len(chunks) == 3
    assert_chunks("x" * 10, content, chunks)


def test_fragmented_content_limit_is_global_and_rejects_before_next_copy_tokenization():
    text = " token=private ".join(["x"] * 8193)
    prepared = prepare_mapped_content(record(text))
    tokenizer = Characters()
    with pytest.raises(MappedContentError, match="mapped_chunk_limit_exceeded"):
        chunk_mapped_content(prepared, tokenizer)
    assert len(tokenizer.calls) == 2 * 8192


def test_random_repeated_wrapper_and_sanitizer_composition():
    import random

    randomizer = random.Random(20260919)
    for _ in range(80):
        body = " ".join(
            randomizer.choices(
                [
                    "same",
                    "\u4e2d\U0001f680e\u0301",
                    "token=same",
                    "--password same",
                    "Authorization: Bearer abcdefghijklmnop",
                    "\t",
                    "https://u:p@host/path",
                ],
                k=10,
            )
        )
        original = "<rules>same</rules><user_query> " + body + " </user_query>"
        content = prepare_mapped_content(record(original))
        assert_map(original, content)
        assert_chunks(original, content, chunk_mapped_content(content, Characters()))


@pytest.mark.parametrize(
    "case", ["output_gap", "source_overlap", "wrong_copy", "missing_exclusion"]
)
def test_bad_upstream_maps_do_not_authorize_original_slices(monkeypatch, case):
    samples = {
        "output_gap": SanitizedInput("body", (SanitizedCopySpan(2, 4, 2, 4),), ()),
        "source_overlap": SanitizedInput(
            "bobo", (SanitizedCopySpan(0, 2, 0, 2), SanitizedCopySpan(2, 4, 0, 2)), ((2, 4),)
        ),
        "wrong_copy": SanitizedInput("BODY", (SanitizedCopySpan(0, 4, 0, 4),), ()),
        "missing_exclusion": SanitizedInput("bo", (SanitizedCopySpan(0, 2, 0, 2),), ()),
    }
    monkeypatch.setattr(mapped, "sanitize_model_input_with_spans", lambda text: samples[case])
    with pytest.raises(MappedContentError, match="mapped_invalid"):
        prepare_mapped_content(record("body"))


def test_assistant_context_keeps_existing_hygiene_semantics_but_not_generated_payloads():
    text = "Discuss <environment_context>literal</environment_context> as context"
    content = prepare_mapped_content(record(text, role="assistant", written_by=WrittenBy.AGENT))
    assert content.state == "ready" and content.actual_text == text
    with pytest.raises(MappedContentError, match="generated"):
        prepare_mapped_content(
            record(
                "<dynamic_tool_catalog>generated</dynamic_tool_catalog>",
                role="assistant",
                written_by=WrittenBy.AGENT,
            )
        )


def test_copy_boundaries_cannot_reconstruct_a_url_across_secret_gap():
    original = "https://example.test/left token=private /right"
    content = prepare_mapped_content(record(original))
    chunks = chunk_mapped_content(content, Characters())
    assert len(chunks) == 2
    assert chunks[0].text == "https://example.test/left "
    assert chunks[1].text == " /right"
    assert chunks[0].end_cp < chunks[1].start_cp
    assert_chunks(original, content, chunks)


def test_source_size_limit_rejects_before_parser(monkeypatch):
    def unexpected(_):
        raise AssertionError("oversize input reached parser")

    monkeypatch.setattr(mapped, "unwrap_host_envelope_with_positions", unexpected)
    with pytest.raises(MappedContentError, match="mapped_source_limit_exceeded"):
        prepare_mapped_content(record("x" * 1_000_001))
