# -*- coding: utf-8 -*-
"""会话级事实 reconciler 测试：INSERT / 同任务推进 SUPERSEDE / 经验链过继"""

import json
import re
from datetime import datetime

import pytest

from hy_memory.coding.fact_reconciler import (
    CHAIN_SUMMARY_PROMPT,
    FACT_RECONCILE_PROMPT,
    WorkingFactReconciler,
)
from hy_memory.coding.types import CodingMemory, WorkingFact, WorkingFactDraft

from tests.coding._helpers import FailingLLMProvider, MockLLMProvider


def _ops_resp(ops):
    return json.dumps({"ops": ops})


# 发生 SUPERSEDE 时 reconciler 会再要一次 LLM 写演变总结，所以 mock 得备第二条
# 回复。不备的话总结那次会拿到 ops JSON 原文，测试现象会很迷惑。
_SUMMARY = "为修复 CI 构建，先定位 Node 18 错误，再升级 workflow；当前构建已通过。"


def _llm(ops, summary=_SUMMARY):
    return MockLLMProvider([_ops_resp(ops), summary])


def _draft(statement, files=None):
    return WorkingFactDraft(statement=statement, files=files or [])


class TestPromptEvidenceContract:
    """只验证 reconciler prompt 契约，不把它冒充为 LLM 行为证明。"""

    def test_candidate_judgement_does_not_upgrade_conjecture(self):
        assert "evidence-bounded text" in FACT_RECONCILE_PROMPT
        assert "shared conjecture/error" in FACT_RECONCILE_PROMPT
        assert "only about task continuation" in FACT_RECONCILE_PROMPT

    def test_chain_summary_preserves_uncertainty_and_blockers(self):
        assert "This is compression only" in CHAIN_SUMMARY_PROMPT
        assert "inferred is not established" in CHAIN_SUMMARY_PROMPT
        assert "unresolved stays unresolved" in CHAIN_SUMMARY_PROMPT
        assert "search/permission/coverage limits" in CHAIN_SUMMARY_PROMPT
        assert "Never upgrade it" in CHAIN_SUMMARY_PROMPT
        assert "directly says that cause was verified" in CHAIN_SUMMARY_PROMPT

    def test_chain_summary_preserves_stages_and_current_status(self):
        assert "every important stage in chronological order" in CHAIN_SUMMARY_PROMPT
        assert "actual action or change" in CHAIN_SUMMARY_PROMPT
        assert "direct result" in CHAIN_SUMMARY_PROMPT
        assert "unresolved issue or limiting condition" in CHAIN_SUMMARY_PROMPT
        assert "explicit current status" in CHAIN_SUMMARY_PROMPT
        assert "chains of 2-3 generations" in CHAIN_SUMMARY_PROMPT
        assert "2-4 compact paragraphs" in CHAIN_SUMMARY_PROMPT
        assert "Do not mechanically copy the full history" in CHAIN_SUMMARY_PROMPT
        assert "Do not invent stage headings or dates" in CHAIN_SUMMARY_PROMPT
        assert "earlier history was omitted" in CHAIN_SUMMARY_PROMPT
        assert "important numeric values" in CHAIN_SUMMARY_PROMPT


@pytest.mark.asyncio
class TestInsert:
    async def test_insert_all_drafts(self, coding_store, mock_embed):
        llm = MockLLMProvider(_ops_resp([]))
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        facts, ops = await rec.reconcile(
            [_draft("事实 A", ["a.py"]), _draft("事实 B")],
            user_id="u1", agent_id="ag1", session_id="s1", workspace_id="ws/a",
        )

        assert [f.statement for f in facts] == ["事实 A", "事实 B"]
        assert [o.action for o in ops] == ["INSERT", "INSERT"]
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored is not None
        assert stored.status == "active"
        assert stored.files == ["a.py"]
        assert stored.agent_id == "ag1"
        assert stored.session_id == "s1"
        assert stored.workspace_id == "ws/a"
        assert stored.fact_link is None

    async def test_no_llm_call_without_candidates(self, coding_store, mock_embed):
        """库里一条候选都没有就没得判，不该白花一次 LLM 调用。"""
        llm = MockLLMProvider(_ops_resp([]))
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        await rec.reconcile([_draft("第一条事实")], user_id="u1")
        assert len(llm.calls) == 0

    async def test_empty_drafts(self, coding_store, mock_embed):
        llm = MockLLMProvider(_ops_resp([]))
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)
        facts, ops = await rec.reconcile([], user_id="u1")
        assert facts == []
        assert ops == []


@pytest.mark.asyncio
class TestSupersede:
    async def _seed_old(self, store, *, derived=None):
        old = WorkingFact(
            fact_id="f_old", user_id="u1",
            statement=(
                "用户要修复 CI 的 npm run build；检查 .github/workflows/ci.yml 后"
                "定位到 Node 18 报 ERR_OSSL_EVP_UNSUPPORTED，尚未修改。"
            ),
            derived_experience_ids=derived or [],
        )
        await store.insert_fact(old)
        return old

    async def test_supersede_marks_old_and_links(self, coding_store, mock_embed):
        await self._seed_old(coding_store)
        llm = _llm([{
            "draft_idx": 0,
            "supersedes": ["f_old"],
            "reason": "继续推进同一个 CI 构建修复",
        }])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        facts, ops = await rec.reconcile(
            [_draft(
                "用户继续修复同一 CI 构建；把 .github/workflows/ci.yml 升到 Node 20，"
                "npm run build 已通过。"
            )], user_id="u1",
        )

        new_id = facts[0].fact_id
        old = await coding_store.get_fact("f_old", user_id="u1")
        assert old.status == "superseded"
        assert old.fact_link == new_id
        assert [o.action for o in ops] == ["SUPERSEDE", "INSERT"]

    async def test_experience_chain_inherited(self, coding_store, mock_embed):
        """旧事实攒下的经验链要过继给新事实，两边都补上。"""
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task="改 config", solution="S",
            source_fact_ids=["f_old"],
        ))
        await self._seed_old(coding_store, derived=["e1"])
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_old"]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        facts, _ = await rec.reconcile(
            [_draft(
                "用户继续修复同一 CI 构建；把 .github/workflows/ci.yml 升到 Node 20，"
                "npm run build 已通过。"
            )], user_id="u1",
        )
        new_id = facts[0].fact_id

        # 正向：新事实拿到旧事实的经验链
        assert facts[0].derived_experience_ids == ["e1"]
        stored_new = await coding_store.get_fact(new_id, user_id="u1")
        assert stored_new.derived_experience_ids == ["e1"]
        # 反向：经验补上新事实，旧的也留着（链路可回溯）
        exp = await coding_store.get_by_id("e1", user_id="u1")
        assert exp.source_fact_ids == ["f_old", new_id]

    async def test_link_chain_stays_walkable(self, coding_store, mock_embed):
        """历史全靠 fact_link 反查还原，所以连续覆盖必须一跳不断。"""
        await self._seed_old(coding_store)          # 第 1 代
        gens = [
            (
                "用户继续修复同一 CI 构建；将 .github/workflows/ci.yml 升到 Node 20，"
                "但 npm run build 仍受旧缓存影响。",
                "f_old",
            ),
            (
                "用户继续验证同一 CI 修复；清理 npm 缓存后 npm run build 通过。",
                None,
            ),
        ]
        prev_id = None
        for stmt, target in gens:
            rec = WorkingFactReconciler(
                coding_store, mock_embed,
                _llm([{"draft_idx": 0, "supersedes": [target or prev_id]}]),
            )
            facts, _ = await rec.reconcile([_draft(stmt)], user_id="u1")
            prev_id = facts[0].fact_id

        lineage = await coding_store.get_fact_ancestors(prev_id, user_id="u1")
        assert [f.statement for f in lineage] == [
            "用户要修复 CI 的 npm run build；检查 .github/workflows/ci.yml 后"
            "定位到 Node 18 报 ERR_OSSL_EVP_UNSUPPORTED，尚未修改。",
            "用户继续修复同一 CI 构建；将 .github/workflows/ci.yml 升到 Node 20，"
            "但 npm run build 仍受旧缓存影响。",
        ]

    async def test_reconcile_prompt_requires_same_task_not_same_file(
        self, coding_store, mock_embed
    ):
        await self._seed_old(coding_store)
        llm = _llm([])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        await rec.reconcile(
            [_draft(
                "用户要给 .github/workflows/ci.yml 增加发布 job；添加 twine upload，"
                "配置完成但尚未发布。"
            )],
            user_id="u1",
        )

        prompt = llm.calls[0]["prompt"]
        assert "继续推进某条已有 session fact 所记录的同一个用户" in prompt
        assert "只是提到了相同 file" in prompt
        assert "追求不同的用户目标" in prompt
        assert "INSERT 是安全默认值" in prompt
        assert "ERR_OSSL_EVP_UNSUPPORTED" in prompt
        assert "twine upload" in prompt

    async def test_summary_empty_when_nothing_superseded(self, coding_store, mock_embed):
        """没覆盖任何东西就没有演变可讲。"""
        llm = MockLLMProvider(_ops_resp([]))
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)
        facts, _ = await rec.reconcile([_draft("全新的结论")], user_id="u1")

        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.fact_chain_summary == ""

    async def test_old_statement_not_indexed_for_retrieval(self, coding_store, mock_embed):
        """被顶掉的说法留在库里但撤出向量库，不该重新具备召回力。"""
        await self._seed_old(coding_store)
        rec = WorkingFactReconciler(
            coding_store, mock_embed, _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
        )
        await rec.reconcile(
            [_draft("config 位于 /etc/app/config.yaml")], user_id="u1",
        )

        # 拿旧说法原文去搜，只能落到新事实（靠两者本身相似），
        # 且召回的 statement 一定是当前值，不是血统里那句
        emb = await coding_store.embed_service.embed("config 位于 /opt/app/config.yaml")
        hits = await coding_store.search_facts(emb, user_id="u1", top=5)
        assert all(f.statement != "config 位于 /opt/app/config.yaml" for f, _ in hits)

    async def test_ignores_id_outside_candidates(self, coding_store, mock_embed):
        """LLM 幻觉出一个不在候选里的 fact_id，不能真去动它。"""
        await self._seed_old(coding_store)
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_hallucinated"]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        _, ops = await rec.reconcile(
            [_draft("config 位于 /etc/app/config.yaml")], user_id="u1",
        )
        assert [o.action for o in ops] == ["INSERT"]
        old = await coding_store.get_fact("f_old", user_id="u1")
        assert old.status == "active"

    async def test_near_duplicate_fact_cannot_cross_authority_scope(
        self, coding_store, mock_embed
    ):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old",
            user_id="u1",
            agent_id="scope:2026-10-18",
            workspace_id="repo-a",
            statement="CI build now passes after upgrading Node",
        ))
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_old"]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        _, ops = await rec.reconcile(
            [_draft("CI build now passes after upgrading Node")],
            user_id="u1",
            agent_id="scope:2026-10-19",
            workspace_id="repo-a",
        )

        old = await coding_store.get_fact("f_old", user_id="u1")
        assert old is not None
        assert old.status == "active"
        assert [op.action for op in ops] == ["INSERT"]
        assert len(llm.calls) == 0

    async def test_old_fact_superseded_only_once(self, coding_store, mock_embed):
        """两条新事实都声称覆盖同一条旧事实时，只让第一条生效。

        否则 fact_link 会被后写的那条覆盖，前一条的过继链就断了。
        """
        await self._seed_old(coding_store)
        llm = _llm([
            {"draft_idx": 0, "supersedes": ["f_old"]},
            {"draft_idx": 1, "supersedes": ["f_old"]},
        ])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        facts, ops = await rec.reconcile(
            [
                _draft("config 位于 /etc/app/config.yaml"),
                _draft("config 位于 /srv/app/config.yaml"),
            ],
            user_id="u1",
        )
        assert len(facts) == 2
        assert sum(1 for o in ops if o.action == "SUPERSEDE") == 1
        old = await coding_store.get_fact("f_old", user_id="u1")
        assert old.fact_link == facts[0].fact_id


@pytest.mark.asyncio
class TestChainSummary:
    """同一任务进展总结由 LLM 写，历史现取自 fact_link 链，不另存。"""

    async def _seed_old(self, store):
        await store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1",
            statement=(
                "用户要迁移 app config；确认当前读取 /opt/app/config.yaml，"
                "尚未修改配置加载逻辑。"
            ),
            created_at=datetime(2026, 1, 1),
        ))

    async def test_llm_summary_stored_and_used(self, coding_store, mock_embed):
        await self._seed_old(coding_store)
        written = (
            "为迁移 app config，先确认 /opt/app/config.yaml，再切到 "
            "/etc/app/config.yaml；当前加载测试通过。"
        )
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}], summary=written),
        )

        facts, _ = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.fact_chain_summary == written

    async def test_prompt_carries_history_and_current(self, coding_store, mock_embed):
        """总结调用要同时看到旧 session 和当前 session，才能写任务进展。"""
        await self._seed_old(coding_store)
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_old"]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        assert len(llm.calls) == 2, "SUPERSEDE 应触发第二次调用"
        prompt = llm.calls[1]["prompt"]
        assert "/opt/app/config.yaml" in prompt
        assert "/etc/app/config.yaml" in prompt
        assert "goal" in prompt
        assert "逐阶段 progression" in prompt
        assert "current status" in prompt
        assert llm.calls[1]["max_tokens"] == 600

    async def test_no_extra_call_without_supersede(self, coding_store, mock_embed):
        """没覆盖就没有演变可总结，不该白花一次调用。"""
        await self._seed_old(coding_store)
        llm = _llm([])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        await rec.reconcile([_draft("另一个话题的结论")], user_id="u1")
        assert len(llm.calls) == 1

    async def test_prompt_carries_full_lineage(self, coding_store, mock_embed):
        """第三代的总结要看到头两代，不能只看到刚被顶掉的那一条。"""
        await self._seed_old(coding_store)
        rec = WorkingFactReconciler(
            coding_store, mock_embed, _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
        )
        facts, _ = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        llm2 = _llm([{"draft_idx": 0, "supersedes": [facts[0].fact_id]}])
        rec2 = WorkingFactReconciler(coding_store, mock_embed, llm2)
        await rec2.reconcile(
            [_draft(
                "用户继续完善同一 config 迁移；改由环境变量 APP_CONFIG 指定路径，"
                "回归测试通过。"
            )], user_id="u1",
        )

        prompt = llm2.calls[1]["prompt"]
        assert "/opt/app/config.yaml" in prompt, "第一代不该丢"
        assert "/etc/app/config.yaml" in prompt
        assert "APP_CONFIG" in prompt
        assert prompt.index("/opt/") < prompt.index("/etc/"), "时间正序"

    async def test_summary_failure_does_not_block_write(self, coding_store, mock_embed):
        """总结挂了，覆盖和入库照常；历史还在库里，下次覆盖会重新总结出来。"""
        await self._seed_old(coding_store)

        class _SummaryFails(MockLLMProvider):
            async def complete(self, prompt, **kwargs):
                if "ENTRIES" in prompt:
                    raise RuntimeError("summary boom")
                return await super().complete(prompt, **kwargs)

        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _SummaryFails(_ops_resp([{"draft_idx": 0, "supersedes": ["f_old"]}])),
        )
        facts, ops = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        assert sum(1 for o in ops if o.action == "SUPERSEDE") == 1
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.fact_chain_summary == ""
        # 关键：这一代没写成总结，但血统没断，还能查回去
        lineage = await coding_store.get_fact_ancestors(stored.fact_id, user_id="u1")
        assert [f.statement for f in lineage] == [
            "用户要迁移 app config；确认当前读取 /opt/app/config.yaml，"
            "尚未修改配置加载逻辑。"
        ]

    async def test_summary_recovers_after_a_failed_generation(
        self, coding_store, mock_embed
    ):
        """某一代没写成总结，下一代重走链子能把漏掉的那代补回来。"""
        await self._seed_old(coding_store)

        class _SummaryFails(MockLLMProvider):
            async def complete(self, prompt, **kwargs):
                if "ENTRIES" in prompt:
                    raise RuntimeError("summary boom")
                return await super().complete(prompt, **kwargs)

        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _SummaryFails(_ops_resp([{"draft_idx": 0, "supersedes": ["f_old"]}])),
        )
        facts, _ = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )
        assert facts[0].fact_chain_summary == ""

        llm2 = _llm([{"draft_idx": 0, "supersedes": [facts[0].fact_id]}])
        rec2 = WorkingFactReconciler(coding_store, mock_embed, llm2)
        await rec2.reconcile(
            [_draft(
                "用户继续完善同一 config 迁移；改由环境变量 APP_CONFIG 指定路径，"
                "回归测试通过。"
            )], user_id="u1",
        )

        prompt = llm2.calls[1]["prompt"]
        assert "/opt/app/config.yaml" in prompt, "上一代漏写的历史要能补回来"

    async def test_deep_lineage_truncated_and_flagged(self, coding_store, mock_embed):
        """长命事实不该把整条链塞进提示词，但得说明省略了几代。"""
        from hy_memory.coding.types import MAX_FACT_CHAIN_ENTRIES

        prev = None
        total = MAX_FACT_CHAIN_ENTRIES + 3
        for i in range(total):
            await coding_store.insert_fact(WorkingFact(
                fact_id=f"g{i}", user_id="u1", statement=f"第 {i} 代结论",
                created_at=datetime(2026, 1, i + 1),
            ))
            if prev:
                await coding_store.supersede_fact(prev, f"g{i}")
            prev = f"g{i}"

        llm = _llm([{"draft_idx": 0, "supersedes": [prev]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)
        await rec.reconcile([_draft("最新结论")], user_id="u1")

        prompt = llm.calls[1]["prompt"]
        assert "第 0 代结论" not in prompt, "最老的几代该被截掉"
        assert f"第 {total - 1} 代结论" in prompt, "最近的要留着"
        assert "omitted" in prompt, "截断了就得标出来"
        # 别报代数。反查本身就只走 MAX+1 步，数不出真实代数，写个数字等于骗
        # 下游——第 30 代覆盖时也只会算出"1 代"，而实际略掉了二十多代。
        assert not re.search(r"\d+\s+earlier", prompt), "截断标注不该带代数"

    async def test_no_truncation_flag_on_short_lineage(self, coding_store, mock_embed):
        """没截断就不该出现省略标注，否则 LLM 会以为还有没给它的历史。"""
        await self._seed_old(coding_store)
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_old"]}])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)
        await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )
        entries = llm.calls[1]["prompt"].split(
            "ENTRIES\n═══════════════════════════════════════════════════════════════════\n",
            1,
        )[1]
        assert "older revisions omitted" not in entries

    async def test_summary_rewritten_each_generation(self, coding_store, mock_embed):
        """第二代覆盖时总结要重写，而不是留着第一代那句。"""
        await self._seed_old(coding_store)
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}], summary="第一版总结"),
        )
        facts, _ = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        rec2 = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": [facts[0].fact_id]}],
                 summary="第二版总结"),
        )
        facts2, _ = await rec2.reconcile(
            [_draft(
                "用户继续完善同一 config 迁移；改由环境变量 APP_CONFIG 指定路径，"
                "回归测试通过。"
            )], user_id="u1",
        )

        stored = await coding_store.get_fact(facts2[0].fact_id, user_id="u1")
        assert stored.fact_chain_summary == "第二版总结"

    async def test_disabled_skips_the_call(self, coding_store, mock_embed):
        """关掉之后不该再多花一次调用，覆盖本身照常。"""
        await self._seed_old(coding_store)
        llm = _llm([{"draft_idx": 0, "supersedes": ["f_old"]}])
        rec = WorkingFactReconciler(
            coding_store, mock_embed, llm, enable_chain_summary=False,
        )
        facts, ops = await rec.reconcile(
            [_draft(
                "用户继续迁移 app config；将加载路径改为 /etc/app/config.yaml，"
                "配置加载测试通过。"
            )], user_id="u1",
        )

        assert len(llm.calls) == 1
        assert sum(1 for o in ops if o.action == "SUPERSEDE") == 1
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.fact_chain_summary == ""


class TestCleanSummary:
    """模型爱给总结加装饰，进库前剥掉。"""

    @pytest.mark.parametrize("raw,expected", [
        ("```\n一句总结。\n```", "一句总结。"),
        ("```markdown\n一句总结。\n```", "一句总结。"),
        ("总结：一句总结。", "一句总结。"),
        ("Summary: 一句总结。", "一句总结。"),
        ("“一句总结。”", "一句总结。"),
        ('"一句总结。"', "一句总结。"),
        ("一句\n总结。", "一句 总结。"),
        ("  一句总结。  ", "一句总结。"),
        ("", ""),
        ("```", ""),
    ])
    def test_strips_decoration(self, raw, expected):
        assert WorkingFactReconciler._clean_summary(raw) == expected

    def test_keeps_inner_quotes(self):
        """只剥最外层的成对引号，句子里引的路径名不该被削掉。"""
        s = WorkingFactReconciler._clean_summary('现在读 "APP_CONFIG" 这个变量。')
        assert s == '现在读 "APP_CONFIG" 这个变量。'


@pytest.mark.asyncio
class TestFailSafe:
    async def test_llm_failure_still_inserts(self, coding_store, mock_embed):
        """LLM 挂了宁可多留一条过期事实，也不能因为一次调用失败丢结论。"""
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="config 位于 /opt/app/config.yaml",
        ))
        rec = WorkingFactReconciler(coding_store, mock_embed, FailingLLMProvider())

        facts, ops = await rec.reconcile(
            [_draft("config 位于 /etc/app/config.yaml")], user_id="u1",
        )
        assert len(facts) == 1
        assert [o.action for o in ops] == ["INSERT"]
        old = await coding_store.get_fact("f_old", user_id="u1")
        assert old.status == "active"

    async def test_unparsable_llm_output(self, coding_store, mock_embed):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="config 位于 /opt/app/config.yaml",
        ))
        rec = WorkingFactReconciler(
            coding_store, mock_embed, MockLLMProvider("不是 JSON 的一段话")
        )

        facts, ops = await rec.reconcile(
            [_draft("config 位于 /etc/app/config.yaml")], user_id="u1",
        )
        assert len(facts) == 1
        assert [o.action for o in ops] == ["INSERT"]

    async def test_markdown_fence_parsed(self, coding_store, mock_embed):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", statement="config 位于 /opt/app/config.yaml",
        ))
        llm = MockLLMProvider([
            "```json\n" + _ops_resp([{"draft_idx": 0, "supersedes": ["f_old"]}]) + "\n```",
            _SUMMARY,
        ])
        rec = WorkingFactReconciler(coding_store, mock_embed, llm)

        _, ops = await rec.reconcile(
            [_draft("config 位于 /etc/app/config.yaml")], user_id="u1",
        )
        assert sum(1 for o in ops if o.action == "SUPERSEDE") == 1


@pytest.mark.asyncio
class TestSubjectGateSwitch:
    """subject 机械闸门的开关。

    这个闸门是精确字符串相等，它的正确性完全取决于 subject 是不是稳定实体名。
    Travel 是（人名，实测 933 组被拦对里明确误判 0 组）；Progressive Search 不是
    （59.2% 是 'Subquery N' 这种位置标签，两个位置标签必然不相等所以必然互拦）。
    所以它必须能按数据集关掉，而不是二选一定死。
    """

    async def _seed(self, store, fact_id, subject, statement):
        await store.insert_fact(WorkingFact(
            fact_id=fact_id, user_id="u1", subject=subject, statement=statement,
        ))

    async def test_gate_on_blocks_cross_subject_supersede(self, coding_store, mock_embed):
        """开启时：主体不同的候选进不了 LLM 名单，所以覆盖不可能发生。"""
        await self._seed(coding_store, "f_emma", "Emma", "Emma 的三日行程已排定，预算待定。")
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_emma"]}]),
            subject_gate=True,
        )
        _, ops = await rec.reconcile(
            [WorkingFactDraft(subject="Bart", statement="Bart 的三日行程已排定，预算待定。")],
            user_id="u1",
        )
        assert [o.action for o in ops] == ["INSERT"]
        assert (await coding_store.get_fact("f_emma", user_id="u1")).status == "active"

    async def test_gate_off_allows_cross_subject_supersede(self, coding_store, mock_embed):
        """关闭时：候选原样交给 LLM，它判覆盖就真覆盖。"""
        await self._seed(coding_store, "f_emma", "Emma", "Emma 的三日行程已排定，预算待定。")
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_emma"]}]),
            subject_gate=False,
        )
        facts, ops = await rec.reconcile(
            [WorkingFactDraft(subject="Bart", statement="Bart 的三日行程已排定，预算待定。")],
            user_id="u1",
        )
        assert [o.action for o in ops] == ["SUPERSEDE", "INSERT"]
        old = await coding_store.get_fact("f_emma", user_id="u1")
        assert old.status == "superseded"
        assert old.fact_link == facts[0].fact_id

    async def test_gate_off_skips_pre_execute_recheck(self, coding_store, mock_embed):
        """关闭时执行前那道复查也要一起跳过，否则闸门没关干净。

        直接调 _execute 绕过召回，模拟"候选闸门放行了但复查还在拦"这个漏关场景。
        """
        await self._seed(coding_store, "f_emma", "Emma", "Emma 的行程。")
        old = await coding_store.get_fact("f_emma", user_id="u1")
        rec = WorkingFactReconciler(
            coding_store, mock_embed, _llm([]), subject_gate=False,
        )
        _, ops = await rec._execute(
            [WorkingFactDraft(subject="Bart", statement="Bart 的行程。")],
            [[old]],
            {0: ["f_emma"]},
            user_id="u1", agent_id="default_agent", session_id=None, workspace_id=None,
        )
        assert "SUPERSEDE" in [o.action for o in ops]

    async def test_empty_subject_never_blocked_either_way(self, coding_store, mock_embed):
        """有一侧 subject 为空时，开关无论怎么设都不该拦。

        空不等于"主体不同"，等于"这次没能定出主体"（兜底存的原文、改动前的老行）。
        拿一个未知去否决一个判断是错的，该交回 LLM 按语义判。
        """
        for gate in (True, False):
            await self._seed(coding_store, f"f_noneed_{gate}", "", "旧的部署脚本结论。")
            old = await coding_store.get_fact(f"f_noneed_{gate}", user_id="u1")
            rec = WorkingFactReconciler(
                coding_store, mock_embed, _llm([]), subject_gate=gate,
            )
            _, ops = await rec._execute(
                [WorkingFactDraft(subject="deploy.sh", statement="新的部署脚本结论。")],
                [[old]],
                {0: [f"f_noneed_{gate}"]},
                user_id="u1", agent_id="default_agent", session_id=None, workspace_id=None,
            )
            assert "SUPERSEDE" in [o.action for o in ops], f"gate={gate} 时空 subject 被误拦"

    async def test_env_var_controls_default(self, coding_store, mock_embed, monkeypatch):
        """环境变量能切默认值，显式构造参数优先于它。"""
        monkeypatch.setenv("HY_FACT_SUBJECT_GATE", "0")
        assert WorkingFactReconciler(coding_store, mock_embed, _llm([])).subject_gate is False
        assert WorkingFactReconciler(
            coding_store, mock_embed, _llm([]), subject_gate=True
        ).subject_gate is True
        monkeypatch.setenv("HY_FACT_SUBJECT_GATE", "1")
        assert WorkingFactReconciler(coding_store, mock_embed, _llm([])).subject_gate is True


@pytest.mark.asyncio
class TestDetailsInheritance:
    """被覆盖事实的 details 过继。

    fact_chain_summary 是 LLM 压缩产物，实测保真度按链距衰减（一条 3 代链里 gen2
    槽位保留 5/5，gen1 保留 0/2，丢的正是具体数值）。过继走复制不走生成，所以
    预算内 100% 保真；且 details 不进向量，追加不改变召回打分。
    """

    async def test_ancestor_details_copied_verbatim(self, coding_store, mock_embed):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", subject="Emma",
            statement="Emma 的行程草案已出，预算待核。",
            details="日均餐费 $32.4-$57.6\n酒店评分 3.2-3.8",
        ))
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
            subject_gate=False, inherit_details=True,
        )
        facts, _ = await rec.reconcile(
            [WorkingFactDraft(subject="Emma", statement="Emma 的行程已定稿。",
                              details="航班 CA1234")],
            user_id="u1",
        )
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        # 本代的 details 在，祖先的具体取值也一字不差地在
        assert "航班 CA1234" in stored.details
        assert "$32.4-$57.6" in stored.details
        assert "3.2-3.8" in stored.details
        assert "gen1" in stored.details and "Emma" in stored.details

    async def test_no_exponential_growth_down_the_chain(self, coding_store, mock_embed):
        """连续覆盖三代，details 不该按代翻倍。

        祖先的 details 里已经含着它自己那代过继来的块，直接追加会指数膨胀。
        血统由 _collect_history 一次走全，所以每代只贡献自己那一份。
        """
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_g1", user_id="u1", subject="T",
            statement="第一代结论。", details="V1=" + "a" * 300,
        ))
        prev = "f_g1"
        sizes = []
        for gen in range(2, 5):
            rec = WorkingFactReconciler(
                coding_store, mock_embed,
                _llm([{"draft_idx": 0, "supersedes": [prev]}]),
                subject_gate=False, inherit_details=True,
            )
            facts, _ = await rec.reconcile(
                [WorkingFactDraft(subject="T", statement=f"第{gen}代结论。",
                                  details=f"V{gen}=" + "a" * 300)],
                user_id="u1",
            )
            prev = facts[0].fact_id
            stored = await coding_store.get_fact(prev, user_id="u1")
            sizes.append(len(stored.details))
            # 每代的取值标记只出现一次，没有被重复过继
            for g in range(1, gen + 1):
                assert stored.details.count(f"V{g}=") == 1, (
                    f"第{gen}代里 V{g}= 出现 {stored.details.count(f'V{g}=')} 次，重复过继了"
                )
        # 线性增长：4 代最多约 4×(300+标记)，指数增长会是 300×2^3 以上
        assert sizes[-1] < 2200, f"details 增长过快: {sizes}"

    async def test_total_budget_enforced_and_loss_reported(self, coding_store, mock_embed):
        """预算是硬的，但被丢掉的代数必须写出来，不静默截断。"""
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", subject="T",
            statement="旧结论。", details="X=" + "b" * 8000,
        ))
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
            subject_gate=False, inherit_details=True,
        )
        facts, _ = await rec.reconcile(
            [WorkingFactDraft(subject="T", statement="新结论。", details="Y=1")],
            user_id="u1",
        )
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert "…(truncated)" in stored.details
        assert len(stored.details) < 6000

    async def test_switch_off_keeps_details_untouched(self, coding_store, mock_embed):
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", subject="T",
            statement="旧结论。", details="祖先取值 Z=9",
        ))
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
            subject_gate=False, inherit_details=False,
        )
        facts, ops = await rec.reconcile(
            [WorkingFactDraft(subject="T", statement="新结论。", details="Y=1")],
            user_id="u1",
        )
        # 覆盖照样发生，只是不过继
        assert "SUPERSEDE" in [o.action for o in ops]
        stored = await coding_store.get_fact(facts[0].fact_id, user_id="u1")
        assert stored.details == "Y=1"
        assert "Z=9" not in stored.details

    async def test_inheritance_does_not_change_recall_vector(self, coding_store, mock_embed):
        """details 不进向量，所以过继多少都不该改变召回打分。

        这是"追加不伤召回质量"这个论断的直接检查，不是推断。
        """
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_old", user_id="u1", subject="T",
            statement="旧结论。", details="无关噪声 " + "q" * 2000,
        ))
        rec = WorkingFactReconciler(
            coding_store, mock_embed,
            _llm([{"draft_idx": 0, "supersedes": ["f_old"]}]),
            subject_gate=False, inherit_details=True,
        )
        facts, _ = await rec.reconcile(
            [WorkingFactDraft(subject="T", statement="新结论。", details="Y=1")],
            user_id="u1",
        )
        new_id = facts[0].fact_id
        emb = await coding_store.embed_service.embed("T 新结论。")
        hits = await coding_store.search_facts(emb, user_id="u1", top=5)
        got = {f.fact_id: s for f, s in hits}
        assert new_id in got
        # 同样的 statement+subject 但不带过继，打分应当一致
        await coding_store.insert_fact(WorkingFact(
            fact_id="f_clean", user_id="u1", subject="T",
            statement="新结论。", details="Y=1",
        ))
        hits2 = await coding_store.search_facts(emb, user_id="u1", top=5)
        got2 = {f.fact_id: s for f, s in hits2}
        assert abs(got2["f_clean"] - got2[new_id]) < 1e-6, (
            f"过继改变了召回打分: clean={got2['f_clean']} inherited={got2[new_id]}"
        )
