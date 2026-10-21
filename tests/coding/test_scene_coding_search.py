# -*- coding: utf-8 -*-
"""
scene="coding" 的**检索侧**端到端 —— 真走 _search_coding / async_search，
不是手搓替身。

与 test_scene_coding_variant.py 的分工：
  那边测「组件」—— 路由解析、prompt 选择、链层自己的 observe/decorate。
  这边测「串起来」—— 真的 embed + 真的 store + 真的 _search_coding 返回段，
                     确认 chain_block 能从库里一路跑到返回值。

复用 tests/coding/ 现成的 fixture（tmp_data_dir / mock_embed / search_ctx），
所以这里的 store 是真的 sqlite + 真的向量检索，只有 embed 是 mock。
"""
import asyncio
import os
import tempfile
import time

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.chain_store import HyCodeMem
from hy_memory.coding.types import WorkingFact

# search_ctx / tmp_data_dir / mock_embed 三个 fixture 从这两处来
from tests.coding.test_search_e2e import search_ctx  # noqa: F401
from tests.coding.conftest import *  # noqa: F401,F403


_STATEMENT = ("On branch feat/q4, in qutebrowser/config/configtypes.py "
              "QtColor._parse_value: settled on the hue multiplier.")
_PATH = "qutebrowser/config/configtypes.py"


class _Msg:
    def __init__(self, role, content, tool_name=None):
        self.role, self.content, self.tool_name = role, content, tool_name
        self.tool_calls = None

    def is_tool_message(self):
        return self.role == "tool"


def _seed_chain(tmpdir, branch="feat/q4", path=_PATH):
    """灌两版进链层。第2版带 "scratch that" —— 撤销标记要被认出来。"""
    H = HyCodeMem(os.path.join(tmpdir, "chain"))
    for said, old, new in [
        ("On branch %s, first pass: 255.0 everywhere." % branch,
         "mult = 255.0\n", "mult = 360.0\n"),
        ("Hold on, scratch that — special-case only hue.",
         "mult = 360.0\n", "mult = 360.0 if kind == 'h' else 255.0\n"),
    ]:
        H.observe(
            HyMemoryClient._chain_text([
                _Msg("user", said),
                _Msg("tool",
                     "path='%s'\nold='''%s'''\nnew='''%s'''" % (path, old, new),
                     tool_name="edit"),
            ]),
            meta={"branch": branch},
        )
    return H


def _enable_chain_block(ctx):
    """search 侧 chain_block 默认关；测注入路径时显式 opt-in。"""
    from hy_memory.config import MemoryConfig

    cfg = getattr(ctx, "_config", None)
    if cfg is None:
        cfg = MemoryConfig()
        ctx._config = cfg
    cfg.coding.chain_block_enabled = True
    ctx._chain_block_enabled = HyMemoryClient._chain_block_enabled.__get__(
        ctx, type(ctx))
    return ctx


def _bind_chain(ctx, store, *, enable_block=True):
    """把链层挂到 search_ctx 这个 SimpleNamespace 上。

    search_ctx 是裸 namespace，不是真 client，所以 _decorate_with_chain 这个
    bound method 得手动绑（它内部会调 self._get_chain_store）。
    """
    ctx._chain_store = store
    ctx._chain_store_failed = False
    ctx._get_chain_store = lambda owner="": store
    ctx._decorate_with_chain = HyMemoryClient._decorate_with_chain.__get__(
        ctx, type(ctx))
    if enable_block:
        _enable_chain_block(ctx)
    return ctx


@pytest.fixture
def chain_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.mark.asyncio
class TestSearchCodingVariantEndToEnd:
    """真调 HyMemoryClient._search_coding，变体走两条路各验一次。"""

    async def _search(self, ctx, query, *, variant, workspace_id=None):
        return await HyMemoryClient._search_coding(
            ctx, target_query=query,
            user_ids=["u1"], workspace_id=workspace_id, branch=None,
            limit=10, request_id="r", t0=time.perf_counter(),
            variant=variant,
        )

    async def _seed_fact(self, ctx):
        await ctx._coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1",
            statement=_STATEMENT, files=[_PATH], branch="feat/q4",
        ))

    async def test_working_variant_has_no_chain_fields(self, search_ctx, chain_dir):
        """★ 回归闸门: variant=working 的返回结构必须与改动前逐字一致。

        即使链层库里**有数据**也不能出现 chain_block —— 否则 working 的调用方会
        突然多收到一个字段。
        """
        await self._seed_fact(search_ctx)
        _bind_chain(search_ctx, _seed_chain(chain_dir))

        resp = await self._search(search_ctx, _STATEMENT, variant="working")
        assert "chain_block" not in resp
        assert "chain_used" not in resp
        assert len(resp["facts"]) == 1      # 向量双层照常工作

    async def test_coding_variant_returns_chain_block(self, search_ctx, chain_dir):
        """variant=coding: 召回的 fact 被挂上链，块里带分支和全部版本。"""
        await self._seed_fact(search_ctx)
        _bind_chain(search_ctx, _seed_chain(chain_dir))

        resp = await self._search(search_ctx, _STATEMENT, variant="coding")
        assert len(resp["facts"]) == 1
        assert resp["chain_used"] == [0]
        blk = resp["chain_block"]
        # 分支名 —— 在错分支上改的代码会被静默丢掉，这一个 token 优先级最高
        assert "feat/q4" in blk
        # 两版都在，不只留最后一版：遇到被弃那版的代码要认得出来
        assert "第1版" in blk and "第2版" in blk
        assert "255.0" in blk and "360.0" in blk

    async def test_coding_variant_chain_block_default_off(self, search_ctx, chain_dir):
        """默认不注入 chain_block（省 token）；库里有链也返回空块。"""
        await self._seed_fact(search_ctx)
        # 挂上真链层，但不开启 chain_block_enabled
        _bind_chain(search_ctx, _seed_chain(chain_dir), enable_block=False)

        resp = await self._search(search_ctx, _STATEMENT, variant="coding")
        assert len(resp["facts"]) == 1
        assert resp["chain_block"] == ""
        assert resp["chain_used"] == []

    async def test_facts_not_deduped(self, search_ctx, chain_dir):
        """★ 挂上链的 fact 仍留在 facts 里 —— 刻意不去重。

        SDK 不悄悄少给数据；要"块 + 剩余 fact"的拼法由调用方按 chain_used 过滤。
        """
        await self._seed_fact(search_ctx)
        _bind_chain(search_ctx, _seed_chain(chain_dir))

        resp = await self._search(search_ctx, _STATEMENT, variant="coding")
        assert resp["chain_used"] == [0]
        assert len(resp["facts"]) == 1, "fact 被链层吃掉了"
        assert resp["facts"][0]["fact_id"] == "f1"

    async def test_empty_chain_store_gives_empty_block(self, search_ctx, chain_dir):
        """链层库是空的 → block 为 ""，不报错，向量双层照常返回。"""
        await self._seed_fact(search_ctx)
        _bind_chain(search_ctx, HyCodeMem(os.path.join(chain_dir, "empty")))

        resp = await self._search(search_ctx, _STATEMENT, variant="coding")
        assert resp["chain_block"] == ""
        assert resp["chain_used"] == []
        assert len(resp["facts"]) == 1

    async def test_chain_unavailable_degrades_to_vector_only(self, search_ctx):
        """链层初始化失败 → 退回纯向量双层，不把整次 search 拖失败。"""
        await self._seed_fact(search_ctx)
        _bind_chain(search_ctx, None)

        resp = await self._search(search_ctx, _STATEMENT, variant="coding")
        assert resp["chain_block"] == ""
        assert len(resp["facts"]) == 1

    async def test_no_fact_hit_gives_no_block(self, search_ctx, chain_dir):
        """向量层一条都没召回 → 没有挂载点，块必空（链层不自己做检索）。"""
        _bind_chain(search_ctx, _seed_chain(chain_dir))

        resp = await self._search(search_ctx, "完全无关的查询", variant="coding")
        assert resp["chain_used"] == []
        assert resp["chain_block"] == ""


@pytest.mark.asyncio
class TestEmptyPathKeysConsistent:
    """fail-safe 的空返回，键集合要与成功路径一致。"""

    async def test_empty_carries_chain_keys_for_coding(self, search_ctx, chain_dir):
        # store 初始化失败 → 走 _empty()。coding 变体下仍应带 chain_* 两个键，
        # 否则调用方对 fail-safe 要写第二套取值逻辑。
        async def _boom():
            raise RuntimeError("store down")
        search_ctx._get_coding_writer = _boom

        resp = await HyMemoryClient._search_coding(
            search_ctx, target_query="x", user_ids=["u1"],
            workspace_id=None, branch=None, limit=10,
            request_id="r", t0=time.perf_counter(), variant="coding",
        )
        assert resp["facts"] == [] and resp["experiences"] == []
        assert resp["chain_block"] == ""
        assert resp["chain_used"] == []

    async def test_empty_has_no_chain_keys_for_working(self, search_ctx):
        async def _boom():
            raise RuntimeError("store down")
        search_ctx._get_coding_writer = _boom

        resp = await HyMemoryClient._search_coding(
            search_ctx, target_query="x", user_ids=["u1"],
            workspace_id=None, branch=None, limit=10,
            request_id="r", t0=time.perf_counter(), variant="working",
        )
        assert "chain_block" not in resp


@pytest.mark.asyncio
class TestAsyncSearchPassesVariant:
    """async_search(scene=...) → _search_coding(variant=...) 这一跳。

    上面那批直接调 _search_coding，绕过了入参映射；这里补的是映射本身。
    """

    _async_search = staticmethod(HyMemoryClient.async_search.__wrapped__)

    async def _capture(self, scene, coding_config):
        """拿 async_search 真的传下去的 variant。

        config 用真的 MemoryConfig（coding_config fixture）而不是手搓 namespace：
        _async_search_impl 会读 _config.llm / _config.search 等好几层，手搓的漏一个
        就报 AttributeError，而那是测试脚手架的缺口、不是被测代码的问题。
        """
        from types import SimpleNamespace
        seen = {}

        async def _spy(target_query, **kw):
            seen.update(kw)
            return {"request_id": "r", "scene": "productivity",
                    "facts": [], "experiences": [], "elapsed_ms": 1.0}

        coding_config.coding.enable = True
        ns = SimpleNamespace(
            _config=coding_config, _cache=None, _search_coding=_spy,
            _coding_llm_provider=None, _history_store=None,
            _enable_history_record=False,
        )
        ns._async_search_impl = HyMemoryClient._async_search_impl.__get__(
            ns, type(ns))
        await self._async_search(
            ns, query="q", scene=scene, search_mode="agentic", user_ids=["u1"],
            workspace_id=None, branch=None, limit=10, min_score=0.0,
        )
        return seen.get("variant")

    async def test_scene_coding_passes_coding_variant(self, coding_config):
        assert await self._capture("coding", coding_config) == "coding"

    async def test_scene_working_passes_working_variant(self, coding_config):
        assert await self._capture("working", coding_config) == "working"

    async def test_legacy_productivity_alias_stays_working(self, coding_config):
        # 旧别名必须保持旧行为 —— 老调用方不该因为新增变体而拿到链块。
        assert await self._capture("productivity", coding_config) == "working"
