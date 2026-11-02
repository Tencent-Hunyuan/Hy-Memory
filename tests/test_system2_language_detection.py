"""System 2 should route technical facts by prose language, not code length."""

import pytest

from hy_memory.pipelines.system2_agent import _detect_language


ZH_FACT = "用户使用 Python 开发，运行 pytest tests/test_search_client.py --maxfail=1"
EN_FACT = "The user runs pytest tests/test_search_client.py --maxfail=1"


@pytest.mark.parametrize("source", ["clusters", "unprocessed_facts"])
@pytest.mark.parametrize("content,expected", [
    (ZH_FACT, "zh"),
    ("用户喜欢咖啡", "zh"),
    (EN_FACT, "en"),
    ("Please update the 中文 README file.", "en"),
    ("", "en"),
    ("  \n\t", "en"),
])
def test_fact_language_ignores_technical_payload_length(source, content, expected):
    facts = [{"content": content}]
    materials = {source: [{"facts": facts}] if source == "clusters" else facts}
    assert _detect_language(materials) == expected


@pytest.mark.parametrize("contents,expected", [
    ([EN_FACT * 20, ZH_FACT, ZH_FACT], "zh"),
    ([ZH_FACT * 20, EN_FACT, EN_FACT], "en"),
    ([ZH_FACT, EN_FACT], "en"),
    (["", "   ", ZH_FACT], "zh"),
    ([], "en"),
])
def test_materials_vote_across_both_fact_sources(contents, expected):
    facts = [{"content": content} for content in contents]
    for ordered in (facts, list(reversed(facts))):
        materials = {
            "clusters": [{"facts": ordered[:1]}],
            "unprocessed_facts": ordered[1:],
        }
        assert _detect_language(materials) == expected
