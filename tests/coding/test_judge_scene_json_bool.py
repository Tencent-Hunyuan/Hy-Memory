# -*- coding: utf-8 -*-
"""旧协议 `{is_coding: bool}` 的兼容解析必须和 `{scene: ...}` 走同一套宽松规则。

模型经常把布尔写成字符串（`"false"` / `"0"`），同文件的 `_parse_scene_label` 已经专门
为这种输出做了映射（`"0" / "false" / "no" / "n"` → chat）。但 `_parse_scene_json` 里
`is_coding` 那条分支用的是裸 `bool()`，而 `bool("false")` 是 `True` —— 同一个取值，
两条解析路径给出相反的场景：chat 段落被判成 working（写进错误的变体库），
chat 查询被路由进生产力召回。
"""

import pytest

from hy_memory.coding.judge import _parse_scene_json


@pytest.mark.parametrize("payload", ['{"is_coding": "false"}', '{"is_coding": "0"}'])
def test_a_string_false_is_chat(payload):
    parsed = _parse_scene_json(payload)

    assert parsed is not None
    assert parsed["scene"] == "chat", payload


@pytest.mark.parametrize("payload", ['{"is_coding": "true"}', '{"is_coding": "1"}'])
def test_a_string_true_is_working(payload):
    parsed = _parse_scene_json(payload)

    assert parsed["scene"] == "working", payload


@pytest.mark.parametrize(
    "payload,expected",
    [
        ('{"is_coding": false}', "chat"),
        ('{"is_coding": 0}', "chat"),
        ('{"is_coding": true}', "working"),
        ('{"is_coding": 1}', "working"),
        # 认不出的取值保持原来的真值语义，不扩大改动面。
        ('{"is_coding": "maybe"}', "working"),
        ('{"is_coding": ""}', "chat"),
        # 显式 scene 优先，is_coding 只是旧字段。
        ('{"scene": "coding", "is_coding": "false"}', "coding"),
    ],
)
def test_existing_is_coding_answers_are_unchanged(payload, expected):
    assert _parse_scene_json(payload)["scene"] == expected
