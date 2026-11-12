# -*- coding: utf-8 -*-
"""chain 版本序数：只有**同一轮**的多个 hunk 合并成一版。

跨轮但用户原话相同时必须是新的一版 —— 否则 "第N版" 的序号会被注入块
引用错位，而且两轮的 diff 被拼进同一版，渲染成一次改了两处。
"""
import os

from hy_memory.coding.chain_store import HyCodeMem


class _Msg:
    def __init__(self, role, content, tool_name=None):
        self.role, self.content, self.tool_name = role, content, tool_name
        self.tool_calls = None

    def is_tool_message(self):
        return self.role == "tool"


def _chain_text(msgs):
    rows = []
    for m in msgs:
        if m.is_tool_message():
            rows.append("[tool:%s] %s" % (m.tool_name or "?", m.content))
        else:
            rows.append("%s: %s" % (m.role, m.content))
    return "\n".join(rows)


def _edit(path, old, new):
    return _Msg("tool", "path='%s'\nold='''%s'''\nnew='''%s'''" % (path, old, new),
                tool_name="edit")


def _rounds(H):
    return H._repository._execute("SELECT seq, patch FROM round ORDER BY seq").fetchall()


def test_same_user_text_in_a_later_turn_starts_a_new_version(tmp_path):
    H = HyCodeMem(os.path.join(str(tmp_path), "chain"))
    H.observe(_chain_text([_Msg("user", "继续"), _edit("x.py", "a = 1\n", "a = 2\n")]),
              meta={"branch": "feat/x"})
    H.observe(_chain_text([_Msg("user", "继续"), _edit("x.py", "a = 2\n", "a = 3\n")]),
              meta={"branch": "feat/x"})
    rows = _rounds(H)
    assert [r[0] for r in rows] == [1, 2], "two turns must stay two versions: %r" % (rows,)


def test_two_edits_in_one_turn_share_one_version(tmp_path):
    H = HyCodeMem(os.path.join(str(tmp_path), "chain"))
    H.observe(_chain_text([
        _Msg("user", "继续"),
        _edit("x.py", "a = 1\n", "a = 2\n"),
        _edit("x.py", "b = 1\n", "b = 2\n"),
    ]), meta={"branch": "feat/x"})
    rows = _rounds(H)
    assert len(rows) == 1, "hunks of one turn must share one version: %r" % (rows,)
