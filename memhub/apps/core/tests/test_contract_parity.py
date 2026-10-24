"""Python 的枚举和 TypeScript 里那份手写的联合类型必须一致。

项目规则写的是「TypeScript 类型由契约生成或验证」，而在这条测试出现之前
两样都没有：`packages/contracts/src/index.ts` 是手抄的，抄错或漏抄不会有
任何东西报警。整套代码生成不在这一轮范围内，但把「唯一来源」这句话对
这几个枚举坐实，成本只有这一个文件。
"""

import re
from pathlib import Path

import pytest

from one_memory.memory_governance import MemoryKind, MemoryOrigin, MemoryStatus

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src" / "index.ts"


def _union(name: str) -> tuple[str, ...]:
    source = CONTRACTS.read_text(encoding="utf-8")
    match = re.search(rf"^export type {name} = (.+);$", source, re.MULTILINE)
    assert match is not None, f"{name} 在 TypeScript 契约里找不到"
    return tuple(sorted(re.findall(r'"([^"]+)"', match.group(1))))


@pytest.mark.parametrize(
    ("name", "enum"),
    [
        ("MemoryStatus", MemoryStatus),
        ("MemoryKind", MemoryKind),
        ("MemoryOrigin", MemoryOrigin),
    ],
)
def test_the_typescript_union_says_what_python_says(name, enum) -> None:
    assert _union(name) == tuple(sorted(member.value for member in enum))


def test_governed_memory_carries_the_same_fields_on_both_sides() -> None:
    from one_memory.memory_governance import GovernedMemory

    source = CONTRACTS.read_text(encoding="utf-8")
    match = re.search(
        r"^export interface GovernedMemory \{\n(.*?)^\}$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    typescript = {
        found.group(1) for found in re.finditer(r"^  (\w+)[?]?:", match.group(1), re.MULTILINE)
    }

    assert typescript == set(GovernedMemory.model_fields)
