# -*- coding: utf-8 -*-
"""Regression: keep_last_k_user_turns(k=0) must keep no turns.

window_start = user_turn_starts[-k] degenerates to [0] when k == 0 (because
-0 == 0), so the window starts at the very first user turn and nothing is
compressed. k=0 should retain zero user turns.
"""

from hy_memory.coding.preproc import keep_last_k_user_turns
from hy_memory.pipelines.base import ChatMessage


def _messages():
    return [
        ChatMessage(role="user", content="turn1"),
        ChatMessage(role="assistant", content="a1"),
        ChatMessage(role="user", content="turn2"),
        ChatMessage(role="assistant", content="a2"),
        ChatMessage(role="user", content="turn3"),
        ChatMessage(role="assistant", content="a3"),
    ]


def test_k_zero_keeps_no_turns():
    assert keep_last_k_user_turns(_messages(), k=0) == []


def test_k_one_keeps_only_last_turn():
    result = keep_last_k_user_turns(_messages(), k=1)
    assert [m.content for m in result] == ["turn3", "a3"]
