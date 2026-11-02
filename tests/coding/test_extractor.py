# -*- coding: utf-8 -*-
"""Extractor 测试（双层抽取）"""

import json

import pytest

from hy_memory.coding.extractor import CodingMemoryExtractor, EXTRACT_PROMPT
from hy_memory.coding.types import CodingWriteError
from hy_memory.pipelines.base import ChatMessage, ToolCall

from tests.coding._helpers import MockLLMProvider, FailingLLMProvider


def _llm_resp(experiences, fact=None):
    return json.dumps({"experiences": experiences, "fact": fact})


def _legacy_llm_resp(experiences, facts=None):
    return json.dumps({"experiences": experiences, "facts": facts or []})


def _msgs():
    return [
        ChatMessage(role="user", content="重构 auth 改成 JWT"),
        ChatMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="1", name="Read", arguments={"path": "auth/middleware.ts"}),
        ]),
        ChatMessage(role="tool", content="42 lines", tool_call_id="1", tool_name="Read"),
    ]


def _assert_verbatim_fallback(facts):
    """断言显式开启后，事实层走了原文兜底。

    判据取 statement 落在用户原话、details 保住完整轨迹这两条。
    """
    assert len(facts) == 1, f"expected exactly one verbatim fact, got {facts!r}"
    f = facts[0]
    assert f.statement == "重构 auth 改成 JWT"
    assert f.subject == ""          # 兜底没有主体可判，留空 → 覆盖判定退回纯语义
    assert "[0] user: 重构 auth 改成 JWT" in f.details
    assert "[2] tool(Read): 42 lines" in f.details


class TestFactPromptContract:
    """静态保护证据忠实度契约；不声称能保证任意 LLM 一定遵守。"""

    def test_only_executed_commands_without_invented_flags(self):
        assert "only actions actually executed" in EXTRACT_PROMPT
        assert "NEVER complete a" in EXTRACT_PROMPT
        assert "command with customary flags" in EXTRACT_PROMPT
        assert '"ran Get-ChildItem -Force" → "ran Get-ChildItem"' in EXTRACT_PROMPT

    def test_partial_negative_findings_keep_scope_and_limits(self):
        assert "Scope every negative finding" in EXTRACT_PROMPT
        assert "enumerate EVERY explicit coverage limit" in EXTRACT_PROMPT
        assert "Never promote a partial search to" in EXTRACT_PROMPT
        assert "Access denied" in EXTRACT_PROMPT
        assert "depth limits" in EXTRACT_PROMPT

    def test_inference_stays_uncertain_and_success_requires_evidence(self):
        assert "inferred, and unresolved" in EXTRACT_PROMPT

        assert "a cause was established" in EXTRACT_PROMPT
        assert "not yet verified" in EXTRACT_PROMPT

    def test_files_contract_includes_investigated_directories(self):
        assert "FILE OR DIRECTORY paths" in EXTRACT_PROMPT
        assert "roots such as `C:\\` and `D:\\`" in EXTRACT_PROMPT
        assert "not paths merely suggested or planned" in EXTRACT_PROMPT

    def test_statement_requires_specific_goal_and_ending_state(self):
        assert "Start with the" in EXTRACT_PROMPT
        assert "specific goal and object" in EXTRACT_PROMPT
        assert "partially completed, failed, blocked, or" in EXTRACT_PROMPT
        assert "still awaiting verification" in EXTRACT_PROMPT

    def test_command_evidence_window_keeps_material_late_flags(self):
        """保护 prompt 输入证据窗口，不声称模型一定会把参数写进 fact。"""
        command = (
            'Get-ChildItem -Path "C:\\\\" -Directory -Filter "new_file_search" '
            "-Recurse -Depth 3 -ErrorAction SilentlyContinue "
            "| Select-Object -First 5 FullName"
        )
        rendered = CodingMemoryExtractor._format_conversation([
            ChatMessage(role="assistant", tool_calls=[
                ToolCall(id="1", name="shell", arguments={"command": command}),
            ]),
        ])
        assert "-Depth 3" in rendered
        assert "-ErrorAction SilentlyContinue" in rendered

    def test_asking_the_user_is_not_a_reusable_experience(self):
        """澄清停滞场景的回归保护。

        如果 Agent 没出计划、只反问缺失字段，
        extractor 把它蒸馏成 "how to ask for departure city and travel dates"
        / "eliciting required fields for travel planning" 等经验，后续检索会继续
        放大反问而不产出可解析计划。停滞被写成技能后会自我复制，所以澄清行为
        必须留在 fact、不进 experiences。
        """
        assert "The act of ASKING THE USER for missing information" in EXTRACT_PROMPT
        assert "conversational" in EXTRACT_PROMPT
        assert '"how to ask for X"' in EXTRACT_PROMPT
        assert "eliciting required fields" in EXTRACT_PROMPT
        # 停滞轮只写 fact，不写经验
        assert 'emit "experiences": [] and write the FACT alone' in EXTRACT_PROMPT
        # 判据取自 extractor 自己对会话状态的判断，不依赖任何评测集字段
        assert "stalled waiting on the" in EXTRACT_PROMPT
        assert "Judge substance by what the tools actually did" in EXTRACT_PROMPT

    def test_mandatory_extraction_exempts_the_stall_case(self):
        """结尾那条 MANDATORY 曾是逼出假经验的直接原因。

        原文是「You MUST output at least ONE experience. Do NOT return an empty
        list.」——末位、无例外。反问轮没有方法可蒸馏，却被禁止返回空，模型只能
        编一条。上面的禁令写在中段，会被这条结尾指令压过去，所以它必须显式让出
        停滞这一种情况。默认仍是抽取：豁免只对停滞开口，不能变成偷懒的借口。
        """
        assert "DEFAULT TO EXTRACTING" in EXTRACT_PROMPT
        assert "The ONE exception is the stall case above" in EXTRACT_PROMPT
        assert "Do not manufacture an experience" in EXTRACT_PROMPT
        assert "never as a shortcut when work was actually done" in EXTRACT_PROMPT
        # 旧的无条件禁令不能复活
        assert "Do NOT return an empty list." not in EXTRACT_PROMPT


@pytest.mark.asyncio
class TestExtractor:
    async def test_new_messages_all_kept_history_only_last_five_user_turns(self):
        llm = MockLLMProvider(_llm_resp([], {"statement": "done"}))
        ext = CodingMemoryExtractor(llm)

        current = []
        for i in range(10):
            current.extend([
                ChatMessage(role="user", content=f"new-user-{i}"),
                ChatMessage(role="assistant", content="A" * 8000),
                ChatMessage(role="tool", content="T" * 5000, tool_name="Read"),
            ])
        history = []
        for i in range(7):
            history.extend([
                ChatMessage(role="user", content=f"history-user-{i}"),
                ChatMessage(role="assistant", content="history answer"),
            ])

        await ext.extract(current, history_messages=history, user_id="u1")
        prompt = llm.calls[0]["prompt"]
        history_block, new_block = prompt.split(
            "NEW MESSAGES (extract only from this complete write):", 1
        )

        assert "history-user-0" not in history_block
        assert "history-user-1" not in history_block
        for i in range(2, 7):
            assert f"history-user-{i}" in history_block

        for i in range(10):
            assert f"new-user-{i}" in new_block
        assert "chars omitted" in new_block
        assert "bytes omitted" in new_block

    async def test_normal_extract(self):
        llm = MockLLMProvider(_llm_resp([
            {
                "task": "Auth 重构",
                "search_keys": ["JWT 实现", "为什么不用 session"],
                "solution": "切到 JWT，文件 auth/middleware.ts",
                "boundary_envs": "node>=18",
                "confidence": 0.9,
            },
        ]))
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        drafts, facts = await ext.extract(
            _msgs(), user_id="u1", agent_id="a1",
            workspace_id="github.com/x/y", branch="main",
        )
        assert len(drafts) == 1
        d = drafts[0]
        assert d.task == "Auth 重构"
        assert d.search_keys == ["JWT 实现", "为什么不用 session"]
        assert d.solution.startswith("切到 JWT")
        assert d.boundary_envs == "node>=18"
        assert d.confidence == 0.9
        assert d.workspace_id == "github.com/x/y"
        assert d.files == ["auth/middleware.ts"]
        # LLM 只给了经验、没给 fact，事实层走原文兜底
        _assert_verbatim_fallback(facts)

    async def test_kept_without_workspace_or_branch(self):
        """缺 workspace_id / branch 不再拒稿。

        边界已不参与召回过滤，再按边界拒稿只会平白丢经验。
        """
        llm = MockLLMProvider(_llm_resp([
            {"task": "T1", "solution": "S", "confidence": 0.9},
            {"task": "T2", "solution": "S", "confidence": 0.9},
        ]))
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(_msgs(), user_id="u1", workspace_id=None, branch=None)
        assert [d.task for d in drafts] == ["T1", "T2"]

    async def test_boundary_envs_defaults_empty(self):
        llm = MockLLMProvider(_llm_resp([{"task": "T", "solution": "S"}]))
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(_msgs(), user_id="u1")
        assert drafts[0].boundary_envs == ""

    async def test_search_keys_dedup_and_truncate(self):
        llm = MockLLMProvider(_llm_resp([{
            "task": "T", "solution": "S",
            "search_keys": ["T", "A", "A", "B", "C", "D", "E", "F"],
            "confidence": 0.8,
        }]))
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(_msgs(), user_id="u1")
        # T 与 task 同名去掉；A 重复去掉；保留前 5
        assert drafts[0].search_keys == ["A", "B", "C", "D", "E"]

    async def test_confidence_clamp(self):
        llm = MockLLMProvider(_llm_resp([
            {"task": "T1", "solution": "S", "confidence": 1.5},
            {"task": "T2", "solution": "S", "confidence": -0.5},
            {"task": "T3", "solution": "S", "confidence": "bad"},
        ]))
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(_msgs(), user_id="u1")
        confs = [d.confidence for d in drafts]
        assert confs == [1.0, 0.0, 0.7]

    async def test_drop_missing_task_or_solution(self):
        llm = MockLLMProvider(_llm_resp([
            {"task": "", "solution": "S"},
            {"task": "T", "solution": ""},
            {"task": "T2", "solution": "S"},
        ]))
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(_msgs(), user_id="u1")
        assert [d.task for d in drafts] == ["T2"]

    async def test_files_auto_extracted(self):
        llm = MockLLMProvider(_llm_resp([
            {"task": "T", "solution": "S", "confidence": 0.8},
        ]))
        msgs = [
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="Read", arguments={"path": "src/auth.py"}),
                ToolCall(id="2", name="Edit", arguments={"file_path": "src/jwt.py"}),
            ]),
        ]
        ext = CodingMemoryExtractor(llm)
        drafts, _ = await ext.extract(msgs, user_id="u1")
        assert drafts[0].files == ["src/auth.py", "src/jwt.py"]

    async def test_empty_memories(self):
        llm = MockLLMProvider(_llm_resp([]))
        ext = CodingMemoryExtractor(llm)
        drafts, facts = await ext.extract(_msgs(), user_id="u1", workspace_id="ws")
        assert drafts == []
        assert facts == []

    async def test_empty_memories_fallback_can_be_enabled(self):
        llm = MockLLMProvider(_llm_resp([]))
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        drafts, facts = await ext.extract(_msgs(), user_id="u1", workspace_id="ws")
        assert drafts == []
        _assert_verbatim_fallback(facts)

    async def test_llm_error_raises_instead_of_empty_success(self):
        """LLM 失败不再返回 ([], [])（等于「成功、0 条」），而是抛 LLM_ERROR。"""
        ext = CodingMemoryExtractor(FailingLLMProvider())
        with pytest.raises(CodingWriteError) as exc:
            await ext.extract(_msgs(), user_id="u1")
        assert exc.value.error_code == "LLM_ERROR"

    async def test_empty_200_reply_raises(self):
        ext = CodingMemoryExtractor(MockLLMProvider(""))
        with pytest.raises(CodingWriteError) as exc:
            await ext.extract(_msgs(), user_id="u1")
        assert exc.value.error_code == "EMPTY_RESPONSE"

    async def test_unparseable_reply_raises(self):
        ext = CodingMemoryExtractor(MockLLMProvider("sorry, I can't do that"))
        with pytest.raises(CodingWriteError) as exc:
            await ext.extract(_msgs(), user_id="u1")
        assert exc.value.error_code == "JSON_PARSE_FAILED"

    async def test_model_answered_nothing_to_remember_is_not_an_error(self):
        """模型答了 {} / 空列表：正常的空抽取，不是失败。"""
        for reply in ("{}", _llm_resp([]), _llm_resp([], None)):
            ext = CodingMemoryExtractor(MockLLMProvider(reply))
            drafts, facts = await ext.extract(_msgs(), user_id="u1")
            assert drafts == [] and facts == []

    async def test_failsafe_on_llm_error_fallback_can_be_enabled(self):
        ext = CodingMemoryExtractor(
            FailingLLMProvider(), verbatim_fallback_enabled=True,
        )
        drafts, facts = await ext.extract(_msgs(), user_id="u1")
        assert drafts == []
        _assert_verbatim_fallback(facts)

    async def test_markdown_fence_parse(self):
        llm = MockLLMProvider(
            "```json\n" + _llm_resp([
                {"task": "T", "solution": "S"},
            ]) + "\n```"
        )
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        drafts, _ = await ext.extract(_msgs(), user_id="u1")
        assert len(drafts) == 1

    async def test_empty_messages(self):
        llm = MockLLMProvider(_llm_resp([]))
        ext = CodingMemoryExtractor(llm)
        drafts, facts = await ext.extract([], user_id="u1")
        assert drafts == []
        # 轨迹本身是空的：兜底判据是"有没有内容"，无内容就该真的什么都不存。
        assert facts == []

    async def test_legacy_memories_key_still_parsed(self):
        """双层化之前 prompt 用的是 "memories"，老模型可能还这么回。"""
        llm = MockLLMProvider(json.dumps({
            "memories": [{"task": "T", "solution": "S"}],
        }))
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        drafts, facts = await ext.extract(_msgs(), user_id="u1")
        assert [d.task for d in drafts] == ["T"]
        _assert_verbatim_fallback(facts)


@pytest.mark.asyncio
class TestFactExtraction:
    async def test_single_fact_object_extracted(self):
        llm = MockLLMProvider(_llm_resp(
            [{"task": "T", "solution": "S"}],
            {
                "statement": (
                    "用户要定位 new_file_search；检查 src/search.py 后确认实现位于该文件，"
                    "并在 C: 和 D: 搜索 foo_config 但未找到。"
                ),
                "files": ["src/search.py"],
            },
        ))
        ext = CodingMemoryExtractor(llm)
        drafts, facts = await ext.extract(_msgs(), user_id="u1")
        assert len(drafts) == 1
        assert [f.statement for f in facts] == [
            "用户要定位 new_file_search；检查 src/search.py 后确认实现位于该文件，"
            "并在 C: 和 D: 搜索 foo_config 但未找到。",
        ]
        assert facts[0].files == ["src/search.py"]

    async def test_facts_only_without_experiences(self):
        """事实全抽不设门槛，所以经验空、事实非空是合法状态。"""
        llm = MockLLMProvider(_llm_resp([], {"statement": "构建产物在 dist/"}))
        ext = CodingMemoryExtractor(llm)
        drafts, facts = await ext.extract(_msgs(), user_id="u1")
        assert drafts == []
        assert [f.statement for f in facts] == ["构建产物在 dist/"]

    async def test_fact_null_falls_back_to_verbatim(self):
        """显式开启时，fact:null 触发原文兜底。"""
        llm = MockLLMProvider(_llm_resp([], None))
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        _, facts = await ext.extract(_msgs(), user_id="u1")
        _assert_verbatim_fallback(facts)

    async def test_fact_missing_statement_falls_back_to_verbatim(self):
        """显式开启时，statement 缺失的 fact 触发原文兜底。"""
        llm = MockLLMProvider(_llm_resp([], {"files": ["b.py"]}))
        ext = CodingMemoryExtractor(llm, verbatim_fallback_enabled=True)
        _, facts = await ext.extract(_msgs(), user_id="u1")
        _assert_verbatim_fallback(facts)

    async def test_tool_only_trace_does_not_create_fallback_by_default(self):
        messages = [
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="exec_command", arguments={"cmd": "pwd"}),
            ]),
            ChatMessage(
                role="tool", content="/workspace", tool_call_id="1",
                tool_name="exec_command",
            ),
        ]
        ext = CodingMemoryExtractor(MockLLMProvider(_llm_resp([], None)))
        _, facts = await ext.extract(messages, user_id="u1")
        assert facts == []

    async def test_tool_only_trace_fallback_can_be_enabled(self):
        messages = [
            ChatMessage(role="assistant", content="", tool_calls=[
                ToolCall(id="1", name="exec_command", arguments={"cmd": "pwd"}),
            ]),
            ChatMessage(
                role="tool", content="/workspace", tool_call_id="1",
                tool_name="exec_command",
            ),
        ]
        ext = CodingMemoryExtractor(
            MockLLMProvider(_llm_resp([], None)),
            verbatim_fallback_enabled=True,
        )
        _, facts = await ext.extract(messages, user_id="u1")
        assert len(facts) == 1
        assert facts[0].statement.startswith(
            '[0] assistant:   [tools: exec_command({"cmd":"pwd"})]'
        )
        assert "[1] tool(exec_command): /workspace" in facts[0].details

    async def test_legacy_three_facts_keeps_first_and_warns(self, caplog):
        llm = MockLLMProvider(_legacy_llm_resp([], [
            {"statement": "第一条会话摘要", "files": ["a.py"]},
            {"statement": "第二条原子事实"},
            {"statement": "第三条原子事实"},
        ]))
        ext = CodingMemoryExtractor(llm)
        with caplog.at_level("WARNING"):
            _, facts = await ext.extract(_msgs(), user_id="u1")
        assert [f.statement for f in facts] == ["第一条会话摘要"]
        assert "legacy 'facts' array with 3 entries" in caplog.text
        assert "keeping the first, dropping 2" in caplog.text

    async def test_fact_files_deduped(self):
        llm = MockLLMProvider(_llm_resp([],
            {
                "statement": "S",
                "files": ["C:\\", "C:\\", " D:\\ ", "src/a.py", ""],
            },
        ))
        ext = CodingMemoryExtractor(llm)
        _, facts = await ext.extract(_msgs(), user_id="u1")
        assert facts[0].files == ["C:\\", "D:\\", "src/a.py"]

    async def test_fact_files_non_list_tolerated(self):
        llm = MockLLMProvider(_llm_resp([], {"statement": "S", "files": "a.py"}))
        ext = CodingMemoryExtractor(llm)
        _, facts = await ext.extract(_msgs(), user_id="u1")
        assert facts[0].files == []
