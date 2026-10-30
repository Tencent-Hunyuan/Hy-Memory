# -*- coding: utf-8 -*-
"""对话、记忆正文和模型输出只进 DEBUG，不进 INFO / WARNING。

MemHub 等调用方默认以 INFO 运行；这些文本一旦进 INFO，就会写进常驻的
hy_memory.log。这里逐条锁住曾经把正文打在 INFO / WARNING 的日志。
"""

import logging

import pytest

from hy_memory.agent.reconciler import ReconcileOp as AgentReconcileOp
from hy_memory.agent.reconciler import _log_ops_detail
from hy_memory.coding.extractor import CodingMemoryExtractor
from hy_memory.coding.fact_reconciler import WorkingFactReconciler
from hy_memory.coding.judge import classify_and_rewrite_queries
from hy_memory.coding.reconciler import CodingMemoryReconciler
from hy_memory.coding.types import CodingMemory, WorkingFact

from tests.coding._helpers import MockLLMProvider

SECRET = "SECRET-CONVERSATION-TEXT-42"


def _above_debug(caplog):
    return [
        r.getMessage() for r in caplog.records
        if r.name.startswith("hy_memory") and r.levelno > logging.DEBUG
    ]


def _debug(caplog):
    return [
        r.getMessage() for r in caplog.records
        if r.name.startswith("hy_memory") and r.levelno == logging.DEBUG
    ]


def _assert_secret_only_at_debug(caplog):
    assert not [m for m in _above_debug(caplog) if SECRET in m]
    assert [m for m in _debug(caplog) if SECRET in m]


@pytest.mark.asyncio
class TestStoreInsertLogs:
    async def test_experience_task_not_at_info(self, coding_store, caplog):
        caplog.set_level(logging.DEBUG, logger="hy_memory")
        await coding_store.insert(CodingMemory(
            experience_id="e1", user_id="u1", task=SECRET, solution="S",
        ))
        info = [m for m in _above_debug(caplog) if "inserted experience_id=e1" in m]
        assert info
        _assert_secret_only_at_debug(caplog)

    async def test_fact_statement_not_at_info(self, coding_store, caplog):
        caplog.set_level(logging.DEBUG, logger="hy_memory")
        await coding_store.insert_fact(WorkingFact(
            fact_id="f1", user_id="u1", agent_id="a1", session_id="s1",
            workspace_id="ws/a", statement=SECRET,
        ))
        info = [m for m in _above_debug(caplog) if "inserted fact_id=f1" in m]
        assert info
        _assert_secret_only_at_debug(caplog)


class TestParseFailureWarnings:
    """解析失败仍发 WARNING，但只带长度，模型输出进 DEBUG。"""

    @pytest.mark.parametrize("parse", [
        CodingMemoryExtractor._parse_output,
        CodingMemoryReconciler._parse_ops,
        WorkingFactReconciler._parse_ops,
    ])
    def test_warning_keeps_length_drops_text(self, parse, caplog):
        caplog.set_level(logging.DEBUG, logger="hy_memory")
        bad = f"not json {SECRET}"
        parse(bad)
        warnings = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "failed to parse" in r.getMessage()
        ]
        assert warnings and f"({len(bad)} chars)" in warnings[0]
        _assert_secret_only_at_debug(caplog)


@pytest.mark.asyncio
class TestJudgeLogs:
    async def test_rewrite_not_at_info(self, caplog):
        caplog.set_level(logging.DEBUG, logger="hy_memory")
        llm = MockLLMProvider(f"1\n{SECRET}")
        result = await classify_and_rewrite_queries(["q"], llm)
        assert result["rewrite_query"] == SECRET
        _assert_secret_only_at_debug(caplog)

    async def test_unrecognized_head_not_at_warning(self, caplog):
        caplog.set_level(logging.DEBUG, logger="hy_memory")
        llm = MockLLMProvider(f"{SECRET}\nrewrite")
        result = await classify_and_rewrite_queries(["q"], llm)
        assert result["ok"] is False
        _assert_secret_only_at_debug(caplog)


def test_reconciler_ops_detail_is_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="hy_memory")
    _log_ops_detail([AgentReconcileOp(op="ADD", content=SECRET)])
    _assert_secret_only_at_debug(caplog)


class _StopAfterLogging(Exception):
    pass


def _add_client():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hy_memory.client import HyMemoryClient

    client = HyMemoryClient.__new__(HyMemoryClient)
    client._mode = "lite"
    client._config = SimpleNamespace(coding=None)
    writer = SimpleNamespace(
        VERSION="test", write=AsyncMock(side_effect=_StopAfterLogging),
    )
    client._registry = SimpleNamespace(get_writer=AsyncMock(return_value=writer))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    SECRET,
    [{"role": "user", "content": SECRET}],
])
async def test_add_does_not_log_input_at_info(data, caplog):
    caplog.set_level(logging.DEBUG, logger="hy_memory")
    client = _add_client()
    with pytest.raises(_StopAfterLogging):
        await client._async_add_impl(data, user_id="u1")
    assert [m for m in _above_debug(caplog) if m.startswith("[add] writing")]
    _assert_secret_only_at_debug(caplog)
