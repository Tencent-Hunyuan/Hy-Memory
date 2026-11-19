# -*- coding: utf-8 -*-
"""Regression: falsy 0/False scene labels are documented as valid chat markers.

\`_parse_scene_label\` uses \`str(value or "")\`, so integer 0 and boolean False
become "" and are rejected, while 1/True parse correctly. The docstring
explicitly promises compatibility with legacy 0/1 and true/false output.
"""

from hy_memory.coding.judge import _parse_scene_label, _parse_scene_json


def test_int_zero_parses_as_chat():
    assert _parse_scene_label(0) == "chat"


def test_bool_false_parses_as_chat():
    assert _parse_scene_label(False) == "chat"


def test_int_one_and_true_still_parse_as_working():
    assert _parse_scene_label(1) == "working"
    assert _parse_scene_label(True) == "working"


def test_scene_json_with_int_zero():
    assert _parse_scene_json('{"scene": 0}') == {"scene": "chat"}
