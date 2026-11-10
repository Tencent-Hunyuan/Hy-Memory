"""Language routing for prompts, including code-heavy agent inputs."""

import json

import pytest

from hy_memory.utils.lang_detect import (
    _leading_language_window,
    detect_language,
    is_chinese,
)
from hy_memory.agent.reconciler import build_reconcile_prompt


@pytest.mark.parametrize(
    "text,expected",
    [
        ("你好", "zh"),
        ("用户喜欢科幻电影，尤其是星际穿越。", "zh"),
        ("Hello", "en"),
        ("The user likes science fiction movies.", "en"),
        ("请修复 search_client.py 里的 timeout bug", "zh"),
        ("修复 bug: TypeError in function parse_response(request_id)", "zh"),
        ("执行 pytest tests/test_search.py，然后修复失败用例", "zh"),
        ("[user]: 请检查 api_client.py 的 timeout", "zh"),
        ("user: 请检查 api_client.py 的 timeout", "zh"),
        ("Please update the 中文 README file.", "en"),
        ("def search(query): return client.search(query)", "en"),
        ("Check https://example.com/docs and update config", "en"),
    ],
)
def test_detect_language_uses_leading_instruction(text, expected):
    detected = detect_language(text)
    routed = "zh" if detected == "zh" else "en"
    assert routed == expected


def test_leading_window_ignores_role_prefix_and_punctuation():
    assert _leading_language_window("  [user]: 请修复 api_client.py") == "请修复apiclie"


def test_messages_route_from_message_content_not_role_names():
    messages = [
        {"role": "user", "content": "请修复 search_client.py 的超时问题"},
        {"role": "assistant", "content": "I will inspect the implementation."},
    ]
    assert is_chinese(messages=messages) is True


def test_writer_json_messages_route_on_content_not_keys():
    messages = [
        {"role": "user", "content": "请修复 search_client.py 的超时问题"},
        {"role": "assistant", "content": "I will inspect the implementation."},
    ]
    assert is_chinese(json.dumps(messages, ensure_ascii=False)) is True
    assert (
        is_chinese(json.dumps([{"role": "user", "content": "Please fix the bug."}]))
        is False
    )


def test_latest_user_message_beats_initial_assistant_and_structured_keys():
    messages = [
        {"role": "assistant", "content": "The release is ready for deployment."},
        {"role": "user", "content": '[{"type":"text","text":"请用中文记录这个结论"}]'},
    ]
    assert is_chinese(json.dumps(messages, ensure_ascii=False)) is True
    assert is_chinese(messages=messages) is True
    assert is_chinese(messages[1]["content"]) is True


def test_non_message_json_and_malformed_content_keep_original_detection():
    for content in (
        '[{"other":"你好"}]',
        '[{"role":"user","content":"你好"}',
        '[{"type":[],"text":"请记录这个决定"}]',
    ):
        assert is_chinese(content) == (detect_language(content) == "zh")


def test_reconcile_uses_chinese_prompt_for_code_heavy_chinese_instruction():
    memory = (
        "请修复 search_client.py 里的 timeout bug: "
        "TypeError in parse_response(request_id)"
    )
    prompt = build_reconcile_prompt(
        new_memories=[memory],
        new_memories_block=memory,
        existing_memories_block="[]",
        current_date="2026-10-12 16:00",
    )
    assert prompt.startswith("你是一个记忆整合系统")


def test_reconcile_keeps_english_prompt_when_chinese_term_appears_later():
    memory = "Please update the 中文 README file."
    prompt = build_reconcile_prompt(
        new_memories=[memory],
        new_memories_block=memory,
        existing_memories_block="[]",
        current_date="2026-10-12 16:00",
    )
    assert prompt.startswith("You are a memory management system.")


def test_system2_detect_language_handles_tech_heavy_chinese():
    """System2's _detect_language must not misclassify tech-heavy Chinese as en.

    Regression for #243: the old full-text ``zh_ratio >= 0.7`` scan diluted the
    Chinese denominator with paths, timestamps, commands and English terms, so
    developer-facing Chinese memories were classified "en" and their L6 schemas
    emitted in English.
    """
    from hy_memory.pipelines.system2_agent import _detect_language

    materials = {
        "clusters": [
            {
                "facts": [
                    {"content": "修复 search_client.py 的 timeout 异常，使用 asyncio.wait_for"},
                    {"content": "在 C:/Users/tara/project/src/main.py 中实现记忆功能"},
                    {"content": "执行 pytest tests/test_search.py 时出现 TypeError"},
                    {"content": "时间戳 2026-11-10 18:00:00 生成的日志文件 log.txt"},
                    {"content": "用户偏好使用 git commit -m 提交代码"},
                    {"content": "需要调用 API https://example.com/v1/memories 保存记忆"},
                ]
            }
        ],
        "unprocessed_facts": [],
    }
    assert _detect_language(materials) == "zh"


def test_system2_detect_language_english_and_empty():
    from hy_memory.pipelines.system2_agent import _detect_language

    en_materials = {
        "clusters": [
            {
                "facts": [
                    {"content": "The user likes science fiction movies and reading books about space exploration."}
                ]
            }
        ],
        "unprocessed_facts": [],
    }
    assert _detect_language(en_materials) == "en"
    assert _detect_language({}) == "en"
