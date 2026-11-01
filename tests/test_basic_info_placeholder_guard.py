"""P0-1：占位符不得污染 L0 基础画像。

弱模型（qwen3.5-2b）在用户什么都没说时把 basic_info 填成 {"name": "用户"} /
"The user" / "N/A"，每个占位符都 supersede 掉真实值，演化链变成
费天翔 → Fei → 用户 → The user。防御：(a) upsert 占位符守卫 + 读侧愈合；
(c) prompt 硬约束。验收 2（assistant 引用 "name is 用户"）由占位符守卫单独兜住。
本文件覆盖改进建议第 10 节验收 1–4。
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from hy_memory.agent.extractor import EXTRACT_FEW_SHOT_EN
from hy_memory.agent.prompts_zh import EXTRACT_FEW_SHOT_ZH
from hy_memory.agent.tools.basic_profile import (
    _assemble_full_kv_from_nodes,
    is_placeholder_value,
    upsert_basic_profile,
)
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus

FIELDS = ["name", "age", "location", "occupation", "employer"]


class FakeStore:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])

    async def list_by_user(self, **kwargs):
        return [n for n in self.nodes if n.user_id == kwargs.get("user_id")]

    async def get_by_id(self, node_id):
        return next((n for n in self.nodes if n.node_id == node_id), None)

    async def upsert(self, node):
        self.nodes.append(node)

    async def update_payload(self, node_id, updates):
        node = await self.get_by_id(node_id)
        for key, value in updates.items():
            if key == "status" and isinstance(value, str):
                value = MemoryStatus(value)
            setattr(node, key, value)


class FakeEmbed:
    async def embed_queued(self, text):
        return [0.1] * 8


def l0_versions(store):
    return [n for n in store.nodes if n.layer == MemoryLayer.L0_BASIC_INFO]


def write_basic_info(store, llm_basic_info):
    """模拟 writer 的 basic_info 段：LLM 给出的非空 basic_info 直接 upsert。"""
    if not llm_basic_info:
        return None
    return asyncio.run(upsert_basic_profile(
        user_id="u1", kv=llm_basic_info, vector_store=store,
        embed_service=FakeEmbed(), allowed_fields=FIELDS,
    ))


# ── 验收 1：寒暄不产生新的 L0 版本 ──

def test_small_talk_creates_no_l0_version():
    # [user]: 你好 / [assistant]: 我可以帮你。弱模型输出 {"name": "用户"}
    store = FakeStore()
    write_basic_info(store, {"name": "用户"})
    write_basic_info(store, {})
    assert l0_versions(store) == []


def test_placeholder_only_upsert_writes_nothing():
    store = FakeStore()
    for kv in ({"name": "用户"}, {"name": "The user"}, {"name": "N/A"},
               {"location": "unknown", "occupation": "未提供"}, {"name": "name"}):
        res = write_basic_info(store, kv)
        assert res.success and not res.changed
    assert l0_versions(store) == []


# ── 验收 2：assistant 引用 "name is 用户" 不写入（仅靠占位符守卫）──

def test_assistant_quoting_placeholder_does_not_write():
    # [assistant]: 那条 The user's name is 用户. 更说明问题
    store = FakeStore()
    for kv in ({"name": "用户"}, {"name": "The user"}, {"name": "The user."}):
        res = write_basic_info(store, kv)
        assert res.success and not res.changed
    assert l0_versions(store) == []


# ── 验收 3：用户自报姓名 ──

def test_user_states_name():
    # [user]: 我叫费天翔，常住杭州，做 AI 基础设施。
    store = FakeStore()
    res = write_basic_info(store, {
        "name": "费天翔", "location": "杭州", "occupation": "AI 基础设施从业者",
    })
    assert res.success and res.created
    assert l0_versions(store)[0].custom["basic_info_kv"] == {
        "name": "费天翔", "location": "杭州", "occupation": "AI 基础设施从业者",
    }


# ── 验收 4：已有真实值不被后续占位符覆盖 ──

def test_existing_name_not_overwritten_by_placeholder():
    store = FakeStore()
    write_basic_info(store, {"name": "费天翔"})
    assert len(l0_versions(store)) == 1
    for kv in ({"name": "用户"}, {"name": "The user."}, {"name": "N/A"}):
        res = write_basic_info(store, kv)
        assert not res.changed
    assert len(l0_versions(store)) == 1
    assert _assemble_full_kv_from_nodes(list(store.nodes)) == {"name": "费天翔"}


def test_mixed_kv_keeps_real_fields_and_drops_placeholders():
    store = FakeStore()
    res = write_basic_info(store, {"name": "用户", "location": "杭州"})
    assert res.diff_kv == {"location": "杭州"}


def test_already_poisoned_chain_heals_on_read():
    now = datetime.utcnow()

    def node(nid, kv, days_ago):
        return MemoryNode(
            node_id=nid, user_id="u1", agent_id="",
            layer=MemoryLayer.L0_BASIC_INFO, content="profile",
            custom={"basic_info_kv": kv},
            gmt_created=now - timedelta(days=days_ago),
        )

    chain = [
        node("a", {"name": "费天翔"}, 4), node("b", {"name": "Fei"}, 3),
        node("c", {"name": "用户"}, 2), node("d", {"name": "The user"}, 1),
    ]
    assert _assemble_full_kv_from_nodes(chain) == {"name": "Fei"}


@pytest.mark.parametrize("value", [
    "", "  ", "用户", "用户。", "The user", "the user.", "THE USER", "user",
    "The user's", "n/a", "N/A", "na", "None", "null", "unknown", "未提供",
    "未知", "不详", "-", "?", "name", " Name ", "姓名",
])
def test_placeholder_values(value):
    assert is_placeholder_value("name", value)


@pytest.mark.parametrize("field,value", [
    ("name", "费天翔"), ("name", "Fei"), ("name", "Username42"),
    ("age", 30), ("age", 0), ("location", "杭州"), ("employer", "Tencent"),
])
def test_real_values_are_not_placeholders(field, value):
    assert not is_placeholder_value(field, value)


# ── (c) prompt 硬约束 ──

def test_prompts_carry_hard_rule_and_small_talk_counter_example():
    from hy_memory.agent import extractor, prompts_zh

    en_rules = [v for v in vars(extractor).values()
                if isinstance(v, str) and "HARD RULE: fill a field ONLY" in v]
    zh_rules = [v for v in vars(prompts_zh).values()
                if isinstance(v, str) and "**硬性规则**" in v]
    assert len(en_rules) >= 2 and len(zh_rules) >= 2
    assert "small talk has no basic_info" in EXTRACT_FEW_SHOT_EN
    assert "寒暄没有 basic_info" in EXTRACT_FEW_SHOT_ZH
