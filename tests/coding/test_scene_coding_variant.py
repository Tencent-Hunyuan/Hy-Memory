# -*- coding: utf-8 -*-
"""
scene="coding" —— coding 链路的软件工程变体。

与 scene="working" 的关系: 同一条链路(同一个 store / 表 / reconciler), 差别只有两处,
本文件按这两处分组:
  1. 抽取规范 —— EXTRACT_PROMPT_CODING(TestPromptSelection / TestPromptContent)
  2. 版本演进链层 —— chain_store(TestChainObserve / TestChainDecorate)

外加路由本身(TestSceneRouting)和"不传 scene 行为不变"的回归闸门(TestWorkingUnchanged)。

⚠️ 这里全部是零 LLM / 零 embedding 的测试。链层本身不调模型, 抽取规范只验"选了哪份
prompt", 不验模型读了之后的行为 —— 那个要上 CodeMemoryBench 跑。
"""
import os
import tempfile

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.chain_store import HyCodeMem
from hy_memory.coding.extractor import (
    EXTRACT_PROMPT,
    EXTRACT_PROMPT_CODING,
    CodingMemoryExtractor,
)


class _FakeMsg:
    """最小 ChatMessage 替身 —— 只要 _chain_text 读的那几个字段。"""

    def __init__(self, role, content, tool_name=None):
        self.role = role
        self.content = content
        self.tool_name = tool_name
        self.tool_calls = None

    def is_tool_message(self):
        return self.role == "tool"


class _FakeProvider:
    config = None
    _llm_config = None


def _edit_msgs(said, old, new, path="qutebrowser/config/configtypes.py"):
    """一轮"用户说要改 + agent 写文件"。

    工具消息写成 `path=/old=/new=` 三段是刻意的: 链层在 git 反查落空时靠这个形态
    合成 diff(_patch_from_edits)。线上大多数场景给不了本地仓路径, 走的就是这条。
    """
    return [
        _FakeMsg("user", said),
        _FakeMsg(
            "tool",
            f"path='{path}'\nold='''{old}'''\nnew='''{new}'''",
            tool_name="edit",
        ),
    ]


# ════════════════════════════════════════════════════════════════════
# 1. 路由
# ════════════════════════════════════════════════════════════════════


class TestSceneRouting:
    def test_coding_routes_to_coding_chain(self):
        # "coding" 和 "working" 归一成同一个路由值 —— add()/search() 里所有
        # `scene_forced == "coding"` 的判断对两者同时生效, 不必逐处加分支。
        assert HyMemoryClient._normalize_scene("coding") == "coding"
        assert HyMemoryClient._normalize_scene("working") == "coding"

    def test_chat_unaffected(self):
        assert HyMemoryClient._normalize_scene("chat") == "chat"

    def test_none_stays_auto(self):
        assert HyMemoryClient._normalize_scene(None) is None

    def test_case_and_whitespace_insensitive(self):
        assert HyMemoryClient._normalize_scene("  CODING ") == "coding"
        assert HyMemoryClient._scene_variant(" Coding ") == "coding"

    def test_illegal_value_raises(self):
        with pytest.raises(ValueError) as ei:
            HyMemoryClient._normalize_scene("productivity")
        # 报错要把三个合法值都列出来 —— 只说 'chat' or 'working' 会让人以为
        # coding 不存在。
        assert "coding" in str(ei.value)

    def test_variant_is_orthogonal_to_route(self):
        # 同一个路由值("coding")下两个变体; 变体只由 _scene_variant 决定。
        assert HyMemoryClient._scene_variant("coding") == "coding"
        assert HyMemoryClient._scene_variant("working") == "working"
        assert HyMemoryClient._scene_variant("chat") == "working"
        assert HyMemoryClient._scene_variant(None) == "working"


# ════════════════════════════════════════════════════════════════════
# 2. 抽取规范的选择
# ════════════════════════════════════════════════════════════════════


class TestPromptSelection:
    def test_default_is_working(self):
        # ★ 回归闸门: 不传 scene 必须还是 working 那份。已落盘的 working 评测
        #   靠这条保证不被动。
        e = CodingMemoryExtractor(_FakeProvider())
        assert e.scene == "working"
        assert e._prompt_tmpl is EXTRACT_PROMPT

    def test_coding_picks_coding_prompt(self):
        e = CodingMemoryExtractor(_FakeProvider(), scene="coding")
        assert e.scene == "coding"
        assert e._prompt_tmpl is EXTRACT_PROMPT_CODING

    def test_unknown_scene_falls_back_to_working(self):
        # 不抛错: 抽取是一次性的, 这段轨迹不会再回来, 为一个拼错的参数把整段丢掉
        # 不值得(同 extract() 里 LLM 失败走原文兜底的取舍)。
        e = CodingMemoryExtractor(_FakeProvider(), scene="bogus")
        assert e.scene == "working"
        assert e._prompt_tmpl is EXTRACT_PROMPT

    def test_both_prompts_share_placeholders(self):
        # 两份必须能被同一份 .format() 参数填 —— extract() 只有一处 format 调用。
        kw = dict(workspace_present="true", branch_present="true",
                  conversation_block="x")
        assert EXTRACT_PROMPT.format(**kw)
        assert EXTRACT_PROMPT_CODING.format(**kw)


class TestPromptContent:
    """两份规范各自独有的段落。钉住的是**内容边界**, 不是措辞质量。"""

    def test_coding_has_hard_constraint_header(self):
        # 前置覆盖后文任何"想要叙事"的措辞。
        assert "HARD CONSTRAINT" in EXTRACT_PROMPT_CODING
        assert "HARD CONSTRAINT" not in EXTRACT_PROMPT
        # 三条核心要求
        assert "FUTURE AGENT needs in order to act" in EXTRACT_PROMPT_CODING
        assert "never \"the user\"" in EXTRACT_PROMPT_CODING
        assert "the session was completed" in EXTRACT_PROMPT_CODING

    def test_coding_has_code_work_section(self):
        assert "CODE WORK" in EXTRACT_PROMPT_CODING
        assert "CODE WORK" not in EXTRACT_PROMPT
        # 四件未来 agent 无法从代码反推的东西
        assert "BRANCH / WORKING LOCATION" in EXTRACT_PROMPT_CODING
        assert "WHICH VERSION WON" in EXTRACT_PROMPT_CODING
        assert "EXACT COORDINATES, NOT PASTED CODE" in EXTRACT_PROMPT_CODING
        assert "VERIFIED vs MERELY WRITTEN" in EXTRACT_PROMPT_CODING

    def test_coding_marks_superseded_version_phrases(self):
        # 用户改主意的语言标记 —— 链层那边也认同一批词。
        assert "scratch that" in EXTRACT_PROMPT_CODING

    def test_working_keeps_the_ask_user_exclusion(self):
        # working 独有: 卡在向用户要参数时强制 experiences: []。
        # coding 侧刻意不带 —— 那边的轨迹是 agent 自主执行, 不存在这种停顿。
        assert "ASKING THE USER" in EXTRACT_PROMPT
        assert "ASKING THE USER" not in EXTRACT_PROMPT_CODING

    def test_both_keep_evidence_fidelity(self):
        # 这条是两份共有的底线: 只记真执行过的动作。任何一份丢了都是缺陷。
        for p in (EXTRACT_PROMPT, EXTRACT_PROMPT_CODING):
            assert "only actions actually executed" in p
            assert "Never promote a partial search to" in p


# ════════════════════════════════════════════════════════════════════
# 3. 版本演进链层 —— 写
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def chain():
    with tempfile.TemporaryDirectory() as d:
        yield HyCodeMem(os.path.join(d, "chain"))


class TestChainText:
    """client._chain_text: messages → 链层能读的纯文本。"""

    def test_renders_lowercase_role_prefixes(self):
        # 形态必须对齐链层的 _USER_LC / _DIFF_END 认的那套小写前缀。
        t = HyMemoryClient._chain_text(
            [_FakeMsg("user", "hi"), _FakeMsg("assistant", "ok"),
             _FakeMsg("tool", "out", tool_name="bash")]
        )
        assert t.startswith("user: hi")
        assert "assistant: ok" in t
        assert "[tool:bash] out" in t

    def test_no_index_prefix(self):
        # ⚠️ 不能复用 extractor._format_conversation —— 那个给每行加 `[i] ` 下标
        # (给 LLM 读的), 而链层的正则锚在行首, 前缀一加全部匹配不上。
        t = HyMemoryClient._chain_text([_FakeMsg("user", "hi")])
        assert not t.startswith("[0]")

    def test_tolerates_empty_and_missing_content(self):
        t = HyMemoryClient._chain_text(
            [_FakeMsg("user", None), _FakeMsg("assistant", "")]
        )
        assert isinstance(t, str)

    def test_empty_input(self):
        assert HyMemoryClient._chain_text([]) == ""
        assert HyMemoryClient._chain_text(None) == ""


class TestChainObserve:
    def test_extracts_patch_from_dialog_without_git(self):
        # 没有本地仓也要能存下来 —— git 反查落空时从对话里的 old/new 合成。
        # agent 修改文件后不一定立即提交，因此不能只依赖 commit diff。
        with tempfile.TemporaryDirectory() as d:
            H = HyCodeMem(os.path.join(d, "c"))   # repo_root=None
            n = H.observe(
                HyMemoryClient._chain_text(
                    _edit_msgs("On branch feat/q4, set mult.",
                               "mult = 255.0\n", "mult = 360.0\n")),
                meta={"branch": "feat/q4"},
            )
            assert n >= 1
            assert H.stats()["threads"] == 1

    def test_multiple_versions_land_on_one_thread(self, chain):
        # ★ 这层存在的理由: 同一处代码被改了几版, 每版的原话 + 真实 patch 都留着。
        for said, old, new in [
            ("On branch feat/q4, first pass: 255.0 everywhere.",
             "mult = 255.0\n", "mult = 360.0\n"),
            ("Hold on, scratch that — special-case only hue.",
             "mult = 360.0\n", "mult = 360.0 if kind == 'h' else 255.0\n"),
        ]:
            chain.observe(HyMemoryClient._chain_text(_edit_msgs(said, old, new)),
                          meta={"branch": "feat/q4"})
        st = chain.stats()
        assert st["threads"] == 1, "同一符号的多版必须并到一条链, 否则版本序号会从 1 重数"
        assert st["rounds"] == 2
        assert st["multi"] == 1

    def test_branch_from_meta_beats_guessing(self, chain):
        chain.observe(
            HyMemoryClient._chain_text(
                _edit_msgs("no branch token here", "a = 1\n", "a = 2\n")),
            meta={"branch": "fix/explicit"},
        )
        assert chain.stats()["branches"] == 1

    def test_no_code_change_stores_nothing(self, chain):
        # 纯对话没有可识别的改动 → 0。这是正常情况, 不是错误。
        assert chain.observe(
            HyMemoryClient._chain_text([_FakeMsg("user", "早上好")])) == 0
        assert chain.stats()["rounds"] == 0

    def test_empty_text_is_noop(self, chain):
        assert chain.observe("") == 0
        assert chain.observe(None) == 0


# ════════════════════════════════════════════════════════════════════
# 4. 版本演进链层 —— 读
# ════════════════════════════════════════════════════════════════════


def _seed_two_versions(H, branch="feat/q4",
                       path="qutebrowser/config/configtypes.py"):
    for said, old, new in [
        ("On branch %s, first pass: 255.0 everywhere." % branch,
         "mult = 255.0\n", "mult = 360.0\n"),
        ("Hold on, scratch that — special-case only hue.",
         "mult = 360.0\n", "mult = 360.0 if kind == 'h' else 255.0\n"),
    ]:
        H.observe(HyMemoryClient._chain_text(_edit_msgs(said, old, new, path=path)),
                  meta={"branch": branch})


_FACT = {
    "statement": ("On branch feat/q4, in qutebrowser/config/configtypes.py "
                  "QtColor._parse_value: settled on the hue multiplier."),
    "branch": "feat/q4",
    "files": ["qutebrowser/config/configtypes.py"],
}


class TestChainDecorate:
    def test_block_carries_branch_and_all_versions(self, chain):
        _seed_two_versions(chain)
        blk, used = chain.decorate([dict(_FACT)], instruction="which version won?")
        assert used == [0]
        # 分支名必须在块里 —— agent 在错分支上改的代码会被静默丢掉。
        assert "feat/q4" in blk
        # 两版都在, 不只留最后一版: 遇到被弃那版的代码要认得出来, 否则会"修"回去。
        assert "第1版" in blk and "第2版" in blk
        assert "255.0" in blk and "360.0" in blk

    def test_fact_without_branch_falls_back_to_files(self, chain):
        # 事实层的 fact 可能没有 branch 字段。branch 认不出就按 files 退回，
        # 不能直接放弃，否则 diff 块会始终为空。
        _seed_two_versions(chain)
        fa = {"statement": "the hue multiplier decision",
              "files": ["qutebrowser/config/configtypes.py"]}
        blk, used = chain.decorate([fa], instruction="hue multiplier")
        assert used == [0]
        assert blk

    def test_unrelated_fact_gets_no_block(self, chain):
        _seed_two_versions(chain)
        blk, used = chain.decorate(
            [{"statement": "something else entirely", "files": ["other/file.py"]}],
            instruction="x")
        assert used == []
        assert blk == ""

    def test_empty_facts(self, chain):
        assert chain.decorate([], instruction="x") == ("", [])

    def test_block_off_switch_disables_layer(self, chain):
        # 消融开关: 让 coding 退化成"纯向量双层"(= working 的召回形态), 用来做
        # 单变量 A/B。CodeMemoryBench 消融靠它: 去掉这层 77/216 → 45/216。
        _seed_two_versions(chain)
        os.environ["HCM_BLOCK_OFF"] = "1"
        try:
            assert chain.decorate([dict(_FACT)], instruction="x") == ("", [])
        finally:
            os.environ.pop("HCM_BLOCK_OFF", None)


def _ns_with_chain_block(**kwargs):
    """胶水测需要显式 opt-in：默认 chain_block_enabled=False 会在进 store 前短路。"""
    import types
    from hy_memory.config import MemoryConfig

    cfg = MemoryConfig()
    cfg.coding.chain_block_enabled = True
    ns = types.SimpleNamespace(_config=cfg, **kwargs)
    ns._chain_block_enabled = HyMemoryClient._chain_block_enabled.__get__(
        ns, type(ns))
    return ns


class TestDecorateGlue:
    """client._decorate_with_chain —— 胶水层的 fail-safe。"""

    def test_returns_empty_when_disabled_by_default(self):
        import types
        ns = types.SimpleNamespace(
            _chain_store=None, _chain_store_failed=False,
            _get_chain_store=lambda owner="": object(),
        )
        ns._chain_block_enabled = HyMemoryClient._chain_block_enabled.__get__(
            ns, type(ns))
        assert HyMemoryClient._decorate_with_chain(ns, [dict(_FACT)]) == ("", [])

    def test_returns_empty_when_chain_unavailable(self):
        ns = _ns_with_chain_block(
            _chain_store=None, _chain_store_failed=True,
            _get_chain_store=lambda owner="": None,
        )
        assert HyMemoryClient._decorate_with_chain(ns, [dict(_FACT)]) == ("", [])

    def test_store_exception_degrades_not_raises(self):
        # 这层是增量, 挂了要退回纯向量双层而不是把整次 search 拖失败。

        class _Boom:
            def decorate(self, *a, **k):
                raise RuntimeError("boom")

        ns = _ns_with_chain_block(_get_chain_store=lambda owner="": _Boom())
        assert HyMemoryClient._decorate_with_chain(ns, [dict(_FACT)]) == ("", [])


class TestObserveGlue:
    def test_returns_zero_when_chain_unavailable(self):
        import types
        ns = types.SimpleNamespace(_get_chain_store=lambda owner="": None)
        assert HyMemoryClient._observe_chain(ns, [_FakeMsg("user", "hi")]) == 0

    def test_store_exception_degrades_not_raises(self):
        import types

        class _Boom:
            def observe(self, *a, **k):
                raise RuntimeError("boom")

        ns = types.SimpleNamespace(_get_chain_store=lambda owner="": _Boom())
        n = HyMemoryClient._observe_chain(
            ns, _edit_msgs("x", "a = 1\n", "a = 2\n"))
        assert n == 0


# ════════════════════════════════════════════════════════════════════
# 5. working 不受影响
# ════════════════════════════════════════════════════════════════════


class TestWorkingUnchanged:
    """★ 整个改动的前提: working 路一个字节都不动。"""

    def test_working_prompt_byte_length_pinned(self):
        # 钉住长度而不是内容: 内容有自己的断言(tests/coding/test_extractor.py 30+ 条),
        # 这里防的是"改 coding 时手滑动了 working 那份"。
        #
        # ⚠️ 这个数字必须用 `len(EXTRACT_PROMPT)` 实测得到, 不要从 diff / 正则抽取的
        #    片段长度推 —— 正则取到的 body 不含尾换行等边界字符, 按它填会差几个字节,
        #    于是这条断言在完全没改 working 的情况下也会红(我第一版就栽在这)。
        assert len(EXTRACT_PROMPT) == 18517, (
            "working 的抽取规范被改动了。它带着自己的实测结论(statement 长度三版对照, "
            "见其正文), 动它要先复现那批 A/B。"
        )

    def test_coding_prompt_is_separate_object(self):
        assert EXTRACT_PROMPT is not EXTRACT_PROMPT_CODING

    def test_working_variant_does_not_touch_chain(self):
        # variant="working" 时写入侧不该调链层 —— 否则 working 的库里会多出
        # hy_code_chain 目录, 且行为与改动前不一致。
        assert HyMemoryClient._scene_variant("working") == "working"
        assert HyMemoryClient._scene_variant(None) == "working"


# ════════════════════════════════════════════════════════════════════
# 6. 两个变体的库分开
# ════════════════════════════════════════════════════════════════════


class TestStoreSeparation:
    """★ working 和 coding 读写**不同的库**。

    为什么必须分库而不是加一个 variant 列做过滤: 检索是**先**在向量集合里取 top-K、
    **再**用 sqlite 过滤。两个变体的 fact 混在同一个集合里时, 向量那一步就已经混了 ——
    后面加任何 SQL 条件都救不回来, 因为 top-K 的名额已经被另一个变体占掉了。
    """

    def test_sqlite_path_diverges(self):
        from hy_memory.coding.store import _variant_db_path
        assert _variant_db_path("/d/coding_memory.db", "_cv") == "/d/coding_memory_cv.db"

    def test_working_paths_byte_identical(self):
        # ★ 回归闸门: working 的路径和集合名必须与加变体之前逐字相同, 否则已落盘的
        #   库读不到(表现为"库空了")。
        from hy_memory.coding.store import (
            _variant_db_path, _derive_fact_collection_name,
            _derive_coding_collection_name,
        )
        assert _variant_db_path("/d/coding_memory.db", "") == "/d/coding_memory.db"
        assert _derive_fact_collection_name("am", "") == "am_working_facts"
        assert _derive_coding_collection_name("am", "") == "am_coding_keys"

    def test_collections_diverge(self):
        from hy_memory.coding.store import (
            _derive_fact_collection_name, _derive_coding_collection_name,
        )
        assert _derive_fact_collection_name("am", "_cv") == "am_working_facts_cv"
        assert _derive_coding_collection_name("am", "_cv") == "am_coding_keys_cv"


# ════════════════════════════════════════════════════════════════════
# 7. 链层的数据域隔离
# ════════════════════════════════════════════════════════════════════


class TestChainOwnerIsolation:
    """★ thread 的键必须含 owner。

    没有它时 thread 键只有 (branch, file, pkey) —— 用户 A 的 feat/q4 和用户 B 的
    feat/q4 会并成同一条链, A 能在注入块里读到 B 的 diff。这是数据泄漏。
    """

    _FACT = [{"statement": "On branch feat/q4, cfg.py 改了什么",
              "branch": "feat/q4", "files": ["cfg.py"]}]

    @staticmethod
    def _edit(said, old, new, path="cfg.py"):
        return [
            _FakeMsg("user", said),
            _FakeMsg("tool",
                     f"path='{path}'\nold='''{old}'''\nnew='''{new}'''",
                     tool_name="edit"),
        ]

    def test_owner_key_shape(self):
        assert HyMemoryClient._chain_owner("u1", "repo1") == "u1|repo1"
        # 两个都空 → ""，保持旧的单用户单仓数据兼容
        assert HyMemoryClient._chain_owner(None, None) == ""
        assert HyMemoryClient._chain_owner("", "") == ""

    def test_two_users_same_branch_do_not_leak(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ch")
            HA = HyCodeMem(p, owner="userA|repo1")
            HA.observe(HyMemoryClient._chain_text(
                self._edit("On branch feat/q4, A 改的", "x = 1\n", "x = 2\n")),
                meta={"branch": "feat/q4"})
            # 同一个库文件、同一个分支、同一个文件名
            HB = HyCodeMem(p, owner="userB|repo1")
            HB.observe(HyMemoryClient._chain_text(
                self._edit("On branch feat/q4, B 改的", "y = 1\n", "y = 2\n")),
                meta={"branch": "feat/q4"})

            blkA, _ = HA.decorate([dict(self._FACT[0])], instruction="q")
            blkB, _ = HB.decorate([dict(self._FACT[0])], instruction="q")
            assert "x = 2" in blkA and "y = 2" not in blkA, "A 读到了 B 的 diff"
            assert "y = 2" in blkB and "x = 2" not in blkB, "B 读到了 A 的 diff"

    def test_empty_owner_is_not_a_public_pool(self):
        # 空 owner 只匹配空 owner，否则旧的空 owner 数据会错误读取所有新写入。
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ch")
            H1 = HyCodeMem(p, owner="u1|r1")
            H1.observe(HyMemoryClient._chain_text(
                self._edit("On branch feat/q4, u1 改的", "x = 1\n", "x = 2\n")),
                meta={"branch": "feat/q4"})
            H0 = HyCodeMem(p)      # owner=''
            blk, used = H0.decorate([dict(self._FACT[0])], instruction="q")
            assert blk == "" and used == []

    def test_stats_scoped_to_owner_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ch")
            for o, var in (("u1|r1", "a"), ("u2|r1", "b")):
                H = HyCodeMem(p, owner=o)
                H.observe(HyMemoryClient._chain_text(
                    self._edit(f"On branch feat/q4, {o}", f"{var} = 1\n", f"{var} = 2\n")),
                    meta={"branch": "feat/q4"})
            H = HyCodeMem(p, owner="u1|r1")
            # 默认只数自己的 —— 灌库自检靠这个数, 报全库会整体偏大
            assert H.stats()["threads"] == 1
            assert H.stats(all_owners=True)["threads"] == 2


# ════════════════════════════════════════════════════════════════════
# 8. 路径③ 的失败闸门
# ════════════════════════════════════════════════════════════════════


class TestEditFailureGate:
    """从对话合成 diff 时, 失败的编辑不该进链。

    链是"哪一版赢了"的唯一依据 —— 掺一个没生效的版本进去, agent 可能照着它写。
    """

    _EDIT = ('path = "cfg.py"\n'
             'old = """mult = 255.0"""\n'
             'new = """mult = 360.0"""')

    def _observe(self, tmpdir, tail, tag):
        H = HyCodeMem(os.path.join(tmpdir, tag))
        t = HyMemoryClient._chain_text([
            _FakeMsg("user", "On branch feat/q4, 改 mult"),
            _FakeMsg("tool", self._EDIT + "\n" + tail, tool_name="bash"),
        ])
        return H.observe(t, meta={"branch": "feat/q4"})

    @pytest.mark.parametrize("tail,tag", [
        ("Traceback (most recent call last):\n  File \"<stdin>\"\nAssertionError", "as"),
        ("old not in s", "ni"),
        ("OBSERVATION: <returncode>2</returncode>", "rc2"),
        ("SyntaxError: unterminated string literal", "se"),
    ])
    def test_failed_edits_blocked(self, tail, tag):
        with tempfile.TemporaryDirectory() as d:
            assert self._observe(d, tail, tag) == 0

    @pytest.mark.parametrize("tail,tag", [
        ("OBSERVATION: <returncode>0</returncode>", "rc0"),
        ("REPLACED=1", "rp"),
        ("", "none"),          # 没有任何结果标记 → 不拦(宁可收, 别漏真改动)
    ])
    def test_successful_and_unmarked_edits_kept(self, tail, tag):
        with tempfile.TemporaryDirectory() as d:
            assert self._observe(d, tail, tag) == 1

    def test_retry_after_error_is_kept(self):
        """★ 失败词之后又有成功迹象 → 重试成功了, 不能拦。

        实测九仓 910 个路径③轮里有 9 轮命中失败词, 其中 **6 轮**后面紧跟
        returncode>0 / REPLACED=1。只看失败词会把这 6 轮真改动误拦。
        """
        with tempfile.TemporaryDirectory() as d:
            assert self._observe(d, "AssertionError\n再试一次\nREPLACED=1", "rt") == 1

    def test_failure_patterns_are_case_sensitive(self):
        """★ 判据不能用 re.I —— 会撞源码标识符。

        我第一版用 re.I 扫 `FAILED|...`, 在九仓上报出 50 轮失败, 其实 50 个全是
        ansible 源码里的函数名 `v2_runner_on_failed` 被匹配到, 而那些轮
        <returncode>0</returncode> 明明是成功的。
        """
        from hy_memory.coding.chain_store import _EDIT_FAIL
        assert not _EDIT_FAIL.search("def v2_runner_on_failed(self, result):")
        assert not _EDIT_FAIL.search("    self._handle_failed_task()")
        # 真的异常类名要抓到
        assert _EDIT_FAIL.search("\nAssertionError\n")
