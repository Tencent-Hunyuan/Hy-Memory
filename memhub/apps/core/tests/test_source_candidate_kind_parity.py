"""来源根目录类型在三处必须同时存在：python 枚举、TS 契约、前端运行时校验。

起因（issue #139）：「我的应用」永久空白，所有接口 200，但前端 sources 恒为空。
根因是 web 侧 `isSourceResponse` 手写了一份类型清单，只列了 default 与
environment_override；Core 后来加了 user_selected（用户自己选的目录，
`sources/connect` 带 source_roots 时就是它）。一行这样的记录就会让整张列表
被拒，而承载它的 prefetch 不允许抛错上报 —— 于是「应用列表暂时不可读取」，
没有任何一处会红。

TS 联合类型漏一个成员不会让构建失败，这里补上那道守卫；前端改成从契约取清单
之后，剩下的风险只有 python 与 TS 之间再漂移。
"""

import re
from pathlib import Path

from one_memory.imports.sources.registry import RootCandidateKind

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "packages" / "contracts" / "src" / "index.ts"


def _typescript_kinds() -> set[str]:
    source = CONTRACT.read_text(encoding="utf-8")
    match = re.search(
        r"export const CANDIDATE_KINDS = \[(.*?)\] as const;", source, re.DOTALL
    )
    assert match, f"没在 {CONTRACT} 里找到 CANDIDATE_KINDS"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_python_and_the_contract_name_the_same_kinds() -> None:
    python_kinds = {kind.value for kind in RootCandidateKind}
    assert python_kinds == _typescript_kinds(), (
        "来源根目录类型在 python 与 TS 契约之间不一致："
        f"python={sorted(python_kinds)} ts={sorted(_typescript_kinds())}。"
        f"请同步 {CONTRACT.relative_to(REPO_ROOT)} 的 CANDIDATE_KINDS。"
    )
