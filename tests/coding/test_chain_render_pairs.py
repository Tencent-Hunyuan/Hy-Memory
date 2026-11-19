# -*- coding: utf-8 -*-
"""chain 注入块的替换对：`~ 旧 → 新` 只能在**同一个 hunk 内**配对。

一轮对话常同时改两处（`_patch_from_edits` 每个编辑动作产出一个 hunk，
observe 又把同一轮的多个 hunk 合进同一版）。如果先把整版 patch 的 `-`/`+`
行倒进两个列表再按下标配对，hunk 1 多出来的新增行会被配给 hunk 2 的删除行，
渲染成"这个函数改成了另一个函数里的某行"这种并不存在的替换。

这条 diff 是未来 agent 判断"哪一版赢了"的唯一依据，配错等于喂假事实。
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


def _rev(H):
    rows = H._repository._execute(
        "SELECT seq, said, patch FROM round ORDER BY seq").fetchall()
    return H._rev_lines([(r[0], r[1], r[2]) for r in rows])


def test_two_edits_in_one_turn_pair_within_their_own_hunk(tmp_path):
    """第一个 hunk 多出一行时，第二个 hunk 的替换对不能被它顶偏。"""
    H = HyCodeMem(os.path.join(str(tmp_path), "chain"))
    H.observe(_chain_text([
        _Msg("user", "把两处都改掉"),
        # hunk 1: 一行换成两行 → 删除数 < 新增数
        _edit("x.py", "call(a)\n", "call_v2(a)\nextra()\n"),
        # hunk 2: 一对一替换
        _edit("x.py", "other()\n", "other_v2()\n"),
    ]), meta={"branch": "feat/x"})
    lines = _rev(H)[1][0]
    assert "~ other() → other_v2()" in lines, lines
    assert "~ other() → extra()" not in lines, (
        "hunk 1 多出来的新增行被配给了 hunk 2 的删除行: %r" % (lines,)
    )
    assert "+ extra()" in lines, lines


def test_extra_deleted_lines_do_not_leak_into_the_next_hunk(tmp_path):
    """反方向：第一个 hunk 删得多，第二个 hunk 的配对同样不能被顶偏。"""
    H = HyCodeMem(os.path.join(str(tmp_path), "chain"))
    H.observe(_chain_text([
        _Msg("user", "两处都改"),
        # hunk 1: 两行换一行 → 删除数 > 新增数
        _edit("x.py", "a1()\na2()\n", "a3()\n"),
        _edit("x.py", "b1()\n", "b2()\n"),
    ]), meta={"branch": "feat/x"})
    lines = _rev(H)[1][0]
    assert "~ b1() → b2()" in lines, lines
    assert "~ a2() → b1()" not in lines, lines
    assert "- a2()" in lines, lines


def test_a_single_replacement_still_pairs_positionally(tmp_path):
    """正向对照：单个 hunk 内一对一仍然是 `~ 旧 → 新`，没有被上面的改动破坏。"""
    H = HyCodeMem(os.path.join(str(tmp_path), "chain"))
    H.observe(_chain_text([
        _Msg("user", "改掉"),
        _edit("x.py", "split(v, '-')\n", "split(v, '_')\n"),
    ]), meta={"branch": "feat/x"})
    assert _rev(H)[1][0] == ["~ split(v, '-') → split(v, '_')"]
