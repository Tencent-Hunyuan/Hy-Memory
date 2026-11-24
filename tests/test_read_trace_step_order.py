"""read trace 的步骤顺序必须按真实发生时刻，历史行（event_at_ms=0）不能插队到最前。"""

import asyncio
import types
from datetime import datetime

from hy_memory.inspector import MemoryInspector


class _FakeCache:
    def __init__(self, rows):
        self._rows = rows

    async def get_pipeline_logs(self, request_id=None, user_id=None, step=None, limit=100):
        return [dict(row) for row in self._rows]


def _ms(hour, minute, second=0, micro=0):
    return int(datetime(2026, 2, 3, hour, minute, second, micro).timestamp() * 1000)


def _inspector(rows):
    async def run():
        insp = MemoryInspector.__new__(MemoryInspector)
        insp._cache = _FakeCache(rows)
        insp._loop_thread = types.SimpleNamespace(_loop=asyncio.get_running_loop())
        return await insp.async_get_read_trace("req-1")

    return asyncio.run(run())


def test_legacy_rows_without_event_time_do_not_jump_to_the_front():
    """补列之前写入的行 event_at_ms=0：应按 created_at 与其它行一起参与排序。"""

    rows = [
        # 后发生、但缺 event_at_ms 的历史行
        {"step": "READ_RECALL_VEC", "event_at_ms": 0, "created_at": "2026-02-03T10:06:00.000",
         "parsed": "", "prompt": "", "response": ""},
        # 先发生、带 event_at_ms 的新行
        {"step": "READ_REQUEST", "event_at_ms": _ms(10, 5, 0), "created_at": "2026-02-03T10:05:00.000",
         "parsed": "", "prompt": "", "response": ""},
    ]

    trace = _inspector(rows)
    assert [step["step"] for step in trace["steps"]] == ["READ_REQUEST", "READ_RECALL_VEC"]


def test_rows_with_event_time_keep_their_occurrence_order():
    """带 event_at_ms 的行仍按发生时刻排，而不是按入库顺序。"""

    rows = [
        {"step": "READ_SUMMARY", "event_at_ms": _ms(10, 5, 2), "created_at": "2026-02-03T10:05:02.000",
         "parsed": "", "prompt": "", "response": ""},
        {"step": "READ_REQUEST", "event_at_ms": _ms(10, 5, 0), "created_at": "2026-02-03T10:05:00.000",
         "parsed": "", "prompt": "", "response": ""},
    ]

    trace = _inspector(rows)
    assert [step["step"] for step in trace["steps"]] == ["READ_REQUEST", "READ_SUMMARY"]


def test_unparsable_timestamps_sort_last_instead_of_first():
    """两个时间都读不出来时排到最后，且不打乱其它行的相对顺序。"""

    rows = [
        {"step": "READ_RECALL_VEC", "event_at_ms": 0, "created_at": "not-a-date",
         "parsed": "", "prompt": "", "response": ""},
        {"step": "READ_REQUEST", "event_at_ms": _ms(10, 5, 0), "created_at": "2026-02-03T10:05:00.000",
         "parsed": "", "prompt": "", "response": ""},
    ]

    trace = _inspector(rows)
    assert [step["step"] for step in trace["steps"]] == ["READ_REQUEST", "READ_RECALL_VEC"]


def test_normalizes_created_at_forms_python_39_cannot_parse(monkeypatch):
    """3.9/3.10 的 fromisoformat 只认严格 T 分隔，不认 "Z"、也不认空格。

    created_at 由不同写入方落库，这两种写法都会出现；不归一化就会被当成
    「解析失败」整组排到最后。用替身模拟 3.9 的解析器来锁住这条。
    """
    import hy_memory.inspector as inspector_module

    class _Python39Datetime(datetime):
        @classmethod
        def fromisoformat(cls, value):
            if "Z" in value or " " in value:
                raise ValueError("Invalid isoformat string")
            return datetime.fromisoformat(value)

    monkeypatch.setattr(inspector_module, "datetime", _Python39Datetime)

    utc_key = inspector_module._log_time_key(
        {"event_at_ms": 0, "created_at": "2026-02-03T10:06:00Z"}
    )
    space_key = inspector_module._log_time_key(
        {"event_at_ms": 0, "created_at": "2026-02-03 10:05:00"}
    )

    assert utc_key[0] != float("inf")
    assert space_key[0] != float("inf")
    assert space_key[0] < utc_key[0]


def test_missing_created_at_does_not_break_the_sort():
    """两个时间列都缺失时排最后，且不与其它行的键比较出 TypeError。"""
    rows = [
        {"step": "READ_RECALL_VEC", "event_at_ms": 0, "created_at": None,
         "parsed": "", "prompt": "", "response": ""},
        {"step": "READ_REQUEST", "event_at_ms": _ms(10, 5, 0), "created_at": None,
         "parsed": "", "prompt": "", "response": ""},
    ]

    trace = _inspector(rows)
    assert [step["step"] for step in trace["steps"]] == ["READ_REQUEST", "READ_RECALL_VEC"]
