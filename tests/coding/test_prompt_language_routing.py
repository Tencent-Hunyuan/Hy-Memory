"""Working/coding prompt language routing and format-string contracts."""

import json
from string import Formatter

import pytest

from hy_memory.coding.extractor import (
    EXTRACT_PROMPT,
    EXTRACT_PROMPT_CODING,
    CodingMemoryExtractor,
)
from hy_memory.coding.fact_reconciler import (
    CHAIN_SUMMARY_PROMPT,
    FACT_RECONCILE_PROMPT,
    build_chain_summary_prompt,
    build_fact_reconcile_prompt,
    WorkingFactReconciler,
)
from hy_memory.coding.reconciler import (
    RECONCILE_PROMPT,
    CodingMemoryReconciler,
    build_experience_reconcile_prompt,
)
from hy_memory.coding.types import CodingMemory, CodingMemoryDraft, WorkingFact, WorkingFactDraft
from hy_memory.pipelines.base import ChatMessage

from tests.coding._helpers import MockLLMProvider


def _fields(template: str) -> set[str]:
    return {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name
    }


@pytest.fixture
def zh_prompts():
    from hy_memory.coding.prompts_zh import (
        CHAIN_SUMMARY_PROMPT_ZH,
        EXTRACT_PROMPT_CODING_ZH,
        EXTRACT_PROMPT_ZH,
        FACT_RECONCILE_PROMPT_ZH,
        RECONCILE_PROMPT_ZH,
    )

    return {
        "extract": EXTRACT_PROMPT_ZH,
        "extract_coding": EXTRACT_PROMPT_CODING_ZH,
        "reconcile": RECONCILE_PROMPT_ZH,
        "fact_reconcile": FACT_RECONCILE_PROMPT_ZH,
        "chain_summary": CHAIN_SUMMARY_PROMPT_ZH,
    }


def test_chinese_templates_match_english_placeholder_sets(zh_prompts):
    pairs = {
        "extract": EXTRACT_PROMPT,
        "extract_coding": EXTRACT_PROMPT_CODING,
        "reconcile": RECONCILE_PROMPT,
        "fact_reconcile": FACT_RECONCILE_PROMPT,
        "chain_summary": CHAIN_SUMMARY_PROMPT,
    }
    for name, english in pairs.items():
        assert _fields(zh_prompts[name]) == _fields(english), name


def test_all_chinese_templates_format_without_errors(zh_prompts):
    rendered = {
        "extract": zh_prompts["extract"].format(
            workspace_present="true",
            branch_present="false",
            conversation_block="用户：请整理报告",
        ),
        "extract_coding": zh_prompts["extract_coding"].format(
            workspace_present="true",
            branch_present="true",
            conversation_block="用户：请修复 api.py",
        ),
        "reconcile": zh_prompts["reconcile"].format(
            drafts_block="draft[0]",
            candidates_block="（无候选）",
        ),
        "fact_reconcile": zh_prompts["fact_reconcile"].format(
            drafts_block="fact[0]",
            candidates_block="（无候选）",
        ),
        "chain_summary": zh_prompts["chain_summary"].format(
            chain_block="第一阶段\n第二阶段",
        ),
    }
    assert all(rendered.values())


def test_chinese_templates_preserve_core_behavior_contracts(zh_prompts):
    working = zh_prompts["extract"]
    coding = zh_prompts["extract_coding"]
    experience_reconcile = zh_prompts["reconcile"]
    fact_reconcile = zh_prompts["fact_reconcile"]
    chain_summary = zh_prompts["chain_summary"]

    assert "等待用户" in working
    assert '"experiences": []' in working
    assert "不要为了满足本指令而杜撰" in working
    assert "证据" in working and "不要" in working
    assert "subject" in working and "不同" in working

    assert "HARD CONSTRAINT" in coding
    assert "BRANCH / WORKING LOCATION" in coding
    assert "WHICH VERSION WON" in coding
    assert "EXACT COORDINATES, NOT PASTED CODE" in coding
    assert "VERIFIED vs MERELY WRITTEN" in coding

    for action in ("ADD", "UPDATE", "DELETE", "SKIP"):
        assert action in experience_reconcile
    assert "明确证据" in experience_reconcile
    assert "绝不要" in experience_reconcile

    assert "SUPERSEDE" in fact_reconcile
    assert "subject" in fact_reconcile
    assert "同一个实体" in fact_reconcile
    assert "保守" in fact_reconcile

    assert "只能报告" in chain_summary
    assert "证据状态" in chain_summary
    assert "绝不要" in chain_summary
    assert "当前状态" in chain_summary


@pytest.mark.parametrize("scene", ["working", "coding"])
def test_extractor_routes_chinese_new_user_message_to_chinese_prompt(scene, zh_prompts):
    extractor = CodingMemoryExtractor(MockLLMProvider("{}"), scene=scene)
    template, language = extractor._select_prompt_template([
        ChatMessage(role="user", content="请修复 api_client.py 的 timeout 问题"),
        ChatMessage(role="tool", content="Traceback: TypeError at line 100"),
    ])
    expected = zh_prompts["extract_coding" if scene == "coding" else "extract"]
    assert language == "zh-Hans"
    assert template is expected


@pytest.mark.parametrize("scene", ["working", "coding"])
def test_extractor_keeps_english_prompt_when_chinese_term_appears_later(scene):
    extractor = CodingMemoryExtractor(MockLLMProvider("{}"), scene=scene)
    template, language = extractor._select_prompt_template([
        ChatMessage(role="user", content="Please update the 中文 README file."),
    ])
    expected = EXTRACT_PROMPT_CODING if scene == "coding" else EXTRACT_PROMPT
    assert language == "en"
    assert template is expected


def test_experience_reconcile_routes_from_new_drafts_not_candidates():
    drafts = [CodingMemoryDraft(
        task="修复搜索超时",
        solution="修改 search_client.py 的 timeout 处理",
        user_id="u1",
    )]
    candidates = [[CodingMemory(
        experience_id="old",
        user_id="u1",
        task="Maintain the search client",
        solution="Use the legacy retry helper",
    )]]
    prompt, language = build_experience_reconcile_prompt(drafts, candidates)
    assert language == "zh-Hans"
    assert prompt.startswith("你")


def test_experience_reconcile_keeps_english_for_english_leading_draft():
    drafts = [CodingMemoryDraft(
        task="Please update the 中文 README file",
        solution="Keep the existing structure",
        user_id="u1",
    )]
    prompt, language = build_experience_reconcile_prompt(drafts, [[]])
    assert language == "en"
    assert prompt.startswith("You")


def test_fact_reconcile_routes_from_new_fact_statement():
    drafts = [WorkingFactDraft(
        subject="api_client.py",
        statement="请修复 timeout 问题；已定位到 retry 逻辑。",
    )]
    candidates = [[WorkingFact(
        fact_id="old",
        user_id="u1",
        statement="The previous session inspected retry behavior.",
    )]]
    prompt, language = build_fact_reconcile_prompt(drafts, candidates)
    assert language == "zh-Hans"
    assert prompt.startswith("你")


def test_chain_summary_routes_from_current_statement():
    from hy_memory.coding.prompts_zh import CHAIN_SUMMARY_PROMPT_ZH

    prompt, language = build_chain_summary_prompt(
        "(2026-10-11) The previous session inspected timeout behavior.\n"
        "(now) 用户继续修复 timeout，并完成验证。",
        current_statement="用户继续修复 timeout，并完成验证。",
    )
    assert language == "zh-Hans"
    assert prompt == CHAIN_SUMMARY_PROMPT_ZH.format(
        chain_block=(
            "(2026-10-11) The previous session inspected timeout behavior.\n"
            "(now) 用户继续修复 timeout，并完成验证。"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scene", ["working", "coding"])
async def test_chinese_extractor_prompt_runs_and_parses_json(scene):
    response = json.dumps({
        "experiences": [{
            "task": "修复搜索超时",
            "search_keys": ["search timeout"],
            "solution": "修改 api_client.py 并运行测试",
            "boundary_envs": "",
            "confidence": 0.9,
        }],
        "fact": {
            "subject": "api_client.py",
            "statement": "修复搜索超时并完成测试验证。",
            "files": ["api_client.py"],
            "details": "pytest passed",
        },
    }, ensure_ascii=False)
    llm = MockLLMProvider(response)
    extractor = CodingMemoryExtractor(llm, scene=scene)

    drafts, facts = await extractor.extract(
        [ChatMessage(role="user", content="请修复 api_client.py 的 timeout 问题")],
        user_id="u1",
    )

    assert len(drafts) == 1
    assert drafts[0].task == "修复搜索超时"
    assert len(facts) == 1
    assert facts[0].statement == "修复搜索超时并完成测试验证。"
    assert "你" in llm.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_experience_reconciler_sends_chinese_prompt_to_llm():
    llm = MockLLMProvider('{"ops": []}')
    reconciler = CodingMemoryReconciler(None, None, llm)
    drafts = [CodingMemoryDraft(
        task="修复搜索超时",
        solution="修改 api_client.py",
        user_id="u1",
    )]

    await reconciler._llm_decide(drafts, [[]])

    assert llm.calls[0]["prompt"].startswith("你")


@pytest.mark.asyncio
async def test_fact_reconciler_sends_chinese_prompt_to_llm():
    llm = MockLLMProvider('{"ops": []}')
    reconciler = WorkingFactReconciler(None, None, llm)
    drafts = [WorkingFactDraft(
        subject="api_client.py",
        statement="请修复搜索超时并完成验证。",
    )]

    await reconciler._llm_decide(drafts, [[WorkingFact(
        fact_id="old",
        user_id="u1",
        statement="Previously inspected timeout behavior.",
    )]])

    assert llm.calls[0]["prompt"].startswith("你")
