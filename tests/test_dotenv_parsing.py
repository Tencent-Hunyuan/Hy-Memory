# -*- coding: utf-8 -*-
"""
`.env` 解析：行尾注释不能进到值里。

`.env.example` 的每一行都写成 `KEY=value   # 中文说明`，文件头却让用户
"复制为 .env 并填入实际值"。取消注释这种行以后，旧解析器只做 `strip()`，
于是 `MEMORY_PIPELINE_TRACE_ENABLED` 的值变成
`"true     # pipeline_logs 表（...）"`：

* 布尔开关按 `lower() in ("true", "1", "yes")` 判，恒为 False —— trace 静默关闭；
* `MEMORY_LOG_LEVEL=DEBUG   # ...` 经 `getattr(logging, ..., logging.INFO)` 落回 INFO；
* `MEMORY_DATA_DIR=~/.hy_memory   # ...` 直接当成字面路径。

所以这里既测解析规则本身，也把 `.env.example` 整份当输入跑一遍，保证模板里的
每一行都能被解析回它显示的值。
"""

import re
from pathlib import Path

import pytest

from hy_memory.config import parse_dotenv

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_inline_comment_is_not_part_of_the_value():
    text = (
        "MEMORY_PIPELINE_TRACE_ENABLED=true     # pipeline_logs 表（LLM 调用链中间结果）\n"
        "MEMORY_LOG_LEVEL=DEBUG                 # DEBUG | INFO | WARNING | ERROR\n"
        "MEMORY_CROSS_DOMAIN_BEH_THRESHOLD=0.70  # 行为相似度碰撞阈值\n"
        "MEMORY_DATA_DIR=~/.hy_memory          # 数据存储根目录\n"
    )

    values = parse_dotenv(text)

    assert values["MEMORY_PIPELINE_TRACE_ENABLED"] == "true"
    assert values["MEMORY_LOG_LEVEL"] == "DEBUG"
    assert values["MEMORY_CROSS_DOMAIN_BEH_THRESHOLD"] == "0.70"
    assert values["MEMORY_DATA_DIR"] == "~/.hy_memory"


def test_hash_without_leading_whitespace_stays_in_the_value():
    """`value#x` 里的 `#` 是值的一部分，只有空白后的 `#` 才算注释。"""
    assert parse_dotenv("MEMORY_MYSQL_PASSWORD=#p@ss\n")["MEMORY_MYSQL_PASSWORD"] == "#p@ss"
    assert parse_dotenv("MEMORY_COLLECTION_NAME=a#b\n")["MEMORY_COLLECTION_NAME"] == "a#b"


def test_quoted_value_keeps_its_hash_and_drops_the_trailing_comment():
    text = 'MEMORY_LLM_API_KEY="sk-abc # not a comment"  # 引号里的 # 是值\n'

    assert parse_dotenv(text)["MEMORY_LLM_API_KEY"] == "sk-abc # not a comment"


def test_value_that_is_only_a_comment_becomes_empty():
    """`.env.example` 里空默认值的写法：`KEY=` 后面跟的是一句说明。"""
    text = "MEMORY_LOG_DIR=                        # 运行日志根目录\n"

    assert parse_dotenv(text)["MEMORY_LOG_DIR"] == ""


def test_comment_lines_blank_lines_and_lines_without_equals_are_skipped():
    text = (
        "# MEMORY_PIPELINE_TRACE_ENABLED=false\n"
        "\n"
        "# ── 通用 ──\n"
        "MEMORY_MODE=pro\n"
        "   \n"
    )

    assert parse_dotenv(text) == {"MEMORY_MODE": "pro"}


def test_crlf_input_parses_the_same_as_lf():
    """`.env.example` 在仓库里是 CRLF；Windows 用户改完还可能混用。"""
    lf = "MEMORY_LOG_LEVEL=DEBUG   # DEBUG | INFO\nMEMORY_MODE=pro\n"
    crlf = lf.replace("\n", "\r\n")

    assert parse_dotenv(crlf) == parse_dotenv(lf) == {
        "MEMORY_LOG_LEVEL": "DEBUG",
        "MEMORY_MODE": "pro",
    }


def _example_lines():
    """`.env.example` 里的 `KEY=value` 行，取消注释后原样返回。"""
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        body = stripped[1:].strip() if stripped.startswith("#") else stripped
        if "=" not in body:
            continue
        key = body.partition("=")[0].strip()
        # 只留像变量名的行：正文里出现的 `=` 不算
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            lines.append((key, body))
    return lines


def test_example_template_lines_have_no_comment_in_their_values():
    """整份模板当输入：取消注释后，值里不能残留注释或中文说明。"""
    lines = _example_lines()
    assert len(lines) >= 40, "模板结构变了，这个用例需要跟着调整"

    values = {key: parse_dotenv(body)[key] for key, body in lines}

    leaked = {key: value for key, value in values.items() if "#" in value}
    assert not leaked, f"这些值里还带着行尾注释：{leaked}"
    cjk = {key: value for key, value in values.items() if re.search(r"[\u4e00-\u9fff]", value)}
    assert not cjk, f"这些值里还带着中文说明：{cjk}"


@pytest.mark.parametrize(
    "key, expected, kind",
    [
        ("MEMORY_PIPELINE_TRACE_ENABLED", True, "bool"),
        ("MEMORY_ENABLE_GRAPH", False, "bool"),
        ("MEMORY_CROSS_DOMAIN_MIN_BASICS", 5, "int"),
        ("MEMORY_LLM_MAX_TOKENS", 1024, "int"),
        ("MEMORY_EMBEDDING_DIMS", 1024, "int"),
        ("MEMORY_CROSS_DOMAIN_BEH_THRESHOLD", 0.70, "float"),
        ("MEMORY_CROSS_DOMAIN_CON_THRESHOLD", 0.60, "float"),
    ],
)
def test_example_template_values_keep_the_types_the_readers_expect(
    key, expected, kind, monkeypatch
):
    """模板里这些默认值要真能解析成读者期待的类型，而不是落回 default。"""
    from hy_memory.config import _get_env_bool, _get_env_float, _get_env_int

    values = {name: parse_dotenv(body)[name] for name, body in _example_lines()}
    assert key in values, f"{key} 已从 .env.example 移除，这个用例需要跟着调整"
    monkeypatch.setenv(key, values[key])

    if kind == "bool":
        # 解析出来的正文字面量本身就得是 true/false，否则下面的 default 兜底
        # 会让 `false  # 说明` 这种污染值蒙对
        assert values[key].lower() in ("true", "false")
        # 故意把 default 设成相反值：解析成功才可能得到 expected
        assert _get_env_bool(key, not expected) is expected
    elif kind == "int":
        assert _get_env_int(key, -1) == expected
    else:
        assert _get_env_float(key, -1.0) == expected
