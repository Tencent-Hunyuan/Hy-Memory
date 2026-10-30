"""A host that saves our injection back must not mint a new user decision."""


def test_stripping_removes_the_notice_block_as_well_as_the_memory_block() -> None:
    from one_memory.derived_echo import strip_injected_memory

    merged = (
        "把按钮改成蓝色\n"
        '<system-reminder data-role="hook"><memhub_memory_context>\n[1] 深绿色\n'
        "</memhub_memory_context>\n\n<memhub_session_notice>\n来源 codex 修订 1 证据 ev\n"
        "</memhub_session_notice></system-reminder>"
    )
    notice_only = (
        '<system-reminder data-role="hook"><memhub_session_notice>\n来源 codex\n'
        "</memhub_session_notice></system-reminder>\n把按钮改成蓝色"
    )
    assert strip_injected_memory(merged) == "把按钮改成蓝色"
    assert strip_injected_memory(notice_only) == "把按钮改成蓝色"


def test_injected_blocks_are_stripped_in_case_and_spacing_variants() -> None:
    from one_memory.derived_echo import strip_injected_memory

    for opening, closing_tag in (
        ("<MEMHUB_MEMORY_CONTEXT>", "</MEMHUB_MEMORY_CONTEXT>"),
        ("<memhub_memory_context >", "</ memhub_memory_context >"),
        ("< memhub_session_notice>", "< /memhub_session_notice>"),
        ("<Memhub_Session_Notice>", "</memhub_session_notice\n>"),
    ):
        text = f"真正的问题\n{opening}\n回忆出来的内容\n{closing_tag}"
        assert strip_injected_memory(text) == "真正的问题", opening


def test_the_tags_stripped_here_are_the_tags_the_hook_writes() -> None:
    """Two runtimes spell the same format; a rename on one side must fail here."""

    import re
    from pathlib import Path

    from one_memory.derived_echo import _INJECTED_TAGS

    hook = (
        Path(__file__).resolve().parents[3] / "packages/host-bridge/src/recall-injection.ts"
    ).read_text()
    written = set(re.findall(r'^const (?:TAG|NOTICE_TAG) = "([a-z_]+)";', hook, re.MULTILINE))
    assert len(written) == 2
    assert written == set(_INJECTED_TAGS)


def _shared_cases() -> dict[str, list[str]]:
    import json
    from pathlib import Path

    return json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "packages/host-bridge/src/reserved-tags.cases.json"
        ).read_text(encoding="utf-8")
    )


def test_core_recognizes_the_reserved_tags_the_hook_disarms() -> None:
    from one_memory.derived_echo import reserved_tags

    cases = _shared_cases()
    for text in cases["reserved"]:
        assert reserved_tags(text) == [(0, len(text))], text
    for text in cases["ordinary"]:
        assert reserved_tags(text) == [], text


def test_core_strips_an_injected_block_in_any_spelling_the_hook_would_recognize() -> None:
    from one_memory.derived_echo import strip_injected_memory

    cases = _shared_cases()
    for block in cases["blocks"]:
        assert strip_injected_memory(f"前{block}后") == "前后", block
    for text in cases["ordinary"]:
        assert strip_injected_memory(text) == text, text


def test_the_fast_path_knows_every_character_that_reads_as_an_opening_bracket() -> None:
    import sys
    import unicodedata

    from one_memory.derived_echo import _OPENING_BRACKETS

    found = {
        chr(point)
        for point in range(sys.maxunicode + 1)
        if "<" in unicodedata.normalize("NFKC", chr(point)).lower()
    }
    assert found == set(_OPENING_BRACKETS)


def test_many_unmatched_tags_do_not_slow_an_import_down() -> None:
    import time

    from one_memory.derived_echo import strip_injected_memory

    text = "<memhub_memory_context>x" * 20_000
    started = time.perf_counter()
    assert strip_injected_memory(text) == text
    assert time.perf_counter() - started < 5


def test_a_long_message_with_brackets_but_no_tag_is_not_walked_character_by_character() -> None:
    import time

    from one_memory.derived_echo import strip_injected_memory

    text = "<" + "普通中文消息，a < b。" * 400_000
    started = time.perf_counter()
    assert strip_injected_memory(text) == text
    assert time.perf_counter() - started < 1.5
