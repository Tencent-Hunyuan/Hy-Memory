# -*- coding: utf-8 -*-
"""跨符号归并时，非空 symbol 不能被更窄的**无符号** hunk 抹掉。

真实 diff 里经常只有部分 hunk 的 header 带函数名（`@@ -1,50 +1,50 @@ def alpha`），
另一些是裸的（`@@ -10,2 +10,2 @@`）。两者行区间重叠时按设计要归并成一条链，
但归并时"取更窄的那个符号"不能把已有的 `alpha` 换成空串 —— 注入块的链标签
`└ <symbol>` 是 agent 区分"这段历史属于哪个函数"的唯一线索。
"""

import pytest

from hy_memory.coding.chain_repository import SqliteChainRepository


@pytest.fixture()
def repo(tmp_path):
    repository = SqliteChainRepository(str(tmp_path / "chain"), owner="u|w", line_merge=0)
    try:
        yield repository
    finally:
        repository.close()


def _thread(repo, tid, branch="feat/x"):
    rows = {row[0]: row for row in repo.threads_by_branch(branch)}
    return rows[tid]


def test_a_narrower_unnamed_hunk_keeps_the_existing_symbol(repo):
    tid = repo.upsert_thread("feat/x", "a.py", "alpha", 1, 51, pkey="fn")

    again = repo.upsert_thread("feat/x", "a.py", "", 10, 12, pkey="fn")

    assert again == tid, "overlapping hunks must stay one chain"
    row = _thread(repo, tid)
    assert row[2] == "alpha", "symbol must not be dropped by an unnamed hunk: %r" % (row,)
    assert (row[3], row[4]) == (1, 51), "line span must still cover both hunks"


def test_a_narrower_named_hunk_takes_the_symbol_over(repo):
    tid = repo.upsert_thread("feat/x", "a.py", "alpha", 1, 51, pkey="fn")

    repo.upsert_thread("feat/x", "a.py", "gamma", 10, 12, pkey="fn")

    assert _thread(repo, tid)[2] == "gamma"


def test_a_wider_unnamed_hunk_keeps_the_existing_symbol(repo):
    tid = repo.upsert_thread("feat/x", "a.py", "alpha", 10, 12, pkey="fn")

    repo.upsert_thread("feat/x", "a.py", "", 1, 60, pkey="fn")

    assert _thread(repo, tid)[2] == "alpha"
