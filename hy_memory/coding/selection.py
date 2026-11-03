# -*- coding: utf-8 -*-
"""Coding 检索的两步 LLM 筛选（search(agentic=True)）。

召回之后、渲染注入块之前，多问模型两次：

1. **选 fact** —— 向量层召回的每一条 fact 都过一遍：对这条指令有没有用。
   没用的整条拿掉（不进 bullet 列表，也不挂链）。
2. **选链** —— 只对第 1 步留下的 fact 挂上的链：要不要看代码、看哪几版。
   这一步不再丢东西，只决定展开深度；不展开的版本仍留一行原话（序号完整，
   "用我第二次改的那版"这种指代才数得清）。

为什么不一次问完：旧的一次问法只看"挂上了链的 fact"，没挂上链的 fact 根本没被
判断过；而且"这条 fact 没用"和"这条链的代码没用"被混在同一个决定里。

与 `search_mode="agentic"` 无关 —— 那个是 query 的三分类 + 重写，发生在检索之前；
这里是检索之后的注入层筛选。

失败语义：任何一步失败（网络、解析、超时）都返回 None，调用方退回"不做筛选"的
原行为。**不能**因为筛选失败就少给内容 —— 那样失败会伪装成"记忆本来就没有"。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

FACT_PROMPT = """An engineer is about to carry out the instruction below. A memory search returned these facts from the earlier development history of the same repository. Decide, for EACH fact, whether it helps carry out THIS instruction.

Instruction:
{instruction}

Facts:
{facts}

A fact is useful when it is about the code location, the decision, or the feature this instruction refers to — including any fact that tells which branch this work was done on (the engineer has to check that branch out first).
A fact is NOT useful when it is about a different piece of code or a different decision, even if it is from the same file or uses similar words.

Answer JSON only: {{"useful": [indices], "why": "<=20 words"}}"""

CHAIN_PROMPT = """An engineer is about to carry out the instruction below. For each code location involved, the earlier history has several numbered versions (what was said each time, and the code change each time).

Instruction:
{instruction}

History:
{threads}

For each thread, decide whether the engineer needs to see actual code, and which versions:
- need_diff=true when the answer depends on how a specific version was written (e.g. "go back to the second version", "keep what we had before the last change").
- need_diff=false when the one-line description of each version is enough.
- When need_diff=true, list the versions whose code matters (usually the one the instruction points to, plus the one it replaced).

Answer JSON only: {{"threads": {{"<thread id>": {{"need_diff": true|false, "versions": [numbers]}}}}, "why": "<=20 words"}}"""

# 喂给模型的字段上限：它们在**提示词**里，不进最终注入块，所以宁可截短也不要超长
_MAX_INSTRUCTION = 2000
_MAX_STATEMENT = 400
_MAX_SAID = 200
_MAX_SAID_PER_THREAD = 8
_MAX_VERSIONS = 8


def _one_line(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """模型回复 → dict。容忍 ```json 围栏和前后废话。"""
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = "\n".join(line for line in body.split("\n") if not line.startswith("```")).strip()
    start, end = body.find("{"), body.rfind("}")
    for candidate in (body, body[start:end + 1] if start >= 0 and end > start else ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            return obj
    return None


class CodingSelector:
    """两步筛选。一个实例可以反复用；无状态（每次调用自带输入）。"""

    def __init__(self, llm_provider, *, max_tokens: int = 800, temperature: Optional[float] = None):
        self.llm_provider = llm_provider
        self.max_tokens = max_tokens
        self.temperature = temperature

    # ---------------------------------------------------------------- 第 1 步
    async def select_facts(
        self, instruction: str, facts: Sequence[Dict[str, Any]]
    ) -> Optional[Set[int]]:
        """→ 有用的 fact 下标集合；失败返回 None（调用方按"全保留"处理）。"""
        if not facts:
            return set()
        lines = []
        for i, fa in enumerate(facts):
            branch = fa.get("branch") or ""
            where = ", ".join((fa.get("files") or [])[:3])
            meta = " ".join(x for x in (
                f"[branch {branch}]" if branch else "",
                f"[{where}]" if where else "",
            ) if x)
            lines.append(f"[{i}] {meta} {_one_line(fa.get('statement'), _MAX_STATEMENT)}".strip())
        try:
            reply = await self._ask(FACT_PROMPT.format(
                instruction=_one_line(instruction, _MAX_INSTRUCTION),
                facts="\n".join(lines),
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("[coding-select] select_facts failed: %s", e)
            return None
        plan = _parse_json_object(reply)
        if plan is None:
            logger.warning("[coding-select] select_facts: unparsable reply %r", (reply or "")[:120])
            return None
        keep = {
            int(i) for i in (plan.get("useful") or [])
            if isinstance(i, (int, float)) and 0 <= int(i) < len(facts)
        }
        logger.info(
            "[coding-select] facts %d → keep %d (%s)",
            len(facts), len(keep), _one_line(plan.get("why"), 60),
        )
        return keep

    # ---------------------------------------------------------------- 第 2 步
    async def choose_chains(
        self,
        instruction: str,
        picked: Sequence[Tuple[Dict[str, Any], Iterable[Tuple]]],
        rounds_of,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """→ {thread_id: {"versions": [int] | None, "no_body": bool}}；失败返回 None。

        picked 是 decorate() 挂上的 (fact, threads)，rounds_of(tid) 返回该链的历代原话。
        """
        lines: List[str] = []
        tids: List[str] = []
        seen: Set[str] = set()
        for _fa, threads in picked:
            for t in threads:
                tid, file, symbol, lo, hi, nv = t[0], t[1], t[2], t[3], t[4], t[5]
                if tid in seen:
                    continue
                seen.add(tid)
                tids.append(tid)
                where = symbol or f"{file}:L{lo}-{hi}"
                lines.append(f"thread {tid} @ {file} {where} ({nv} versions)")
                for seq, said in enumerate(list(rounds_of(tid))[:_MAX_SAID_PER_THREAD], 1):
                    lines.append(f"    v{seq}: {_one_line(said, _MAX_SAID)}")
        if not tids:
            return None
        try:
            reply = await self._ask(CHAIN_PROMPT.format(
                instruction=_one_line(instruction, _MAX_INSTRUCTION),
                threads="\n".join(lines),
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("[coding-select] choose_chains failed: %s", e)
            return None
        plan = _parse_json_object(reply)
        if plan is None:
            logger.warning("[coding-select] choose_chains: unparsable reply %r", (reply or "")[:120])
            return None
        spec = plan.get("threads") or {}
        if not isinstance(spec, dict):
            return None
        out: Dict[str, Dict[str, Any]] = {}
        for tid in tids:
            item = spec.get(str(tid))
            if not isinstance(item, dict):
                continue  # 没提到 = 不动，按渲染层的默认策略
            if item.get("need_diff") is False:
                out[tid] = {"versions": None, "no_body": True}
                continue
            versions = item.get("versions")
            if isinstance(versions, list):
                nums = [int(v) for v in versions if isinstance(v, (int, float)) and int(v) >= 1]
                out[tid] = {"versions": sorted(set(nums))[:_MAX_VERSIONS], "no_body": False}
        logger.info(
            "[coding-select] chains %d → decided %d (%s)",
            len(tids), len(out), _one_line(plan.get("why"), 60),
        )
        return out

    # ---------------------------------------------------------------- 调用
    async def _ask(self, prompt: str) -> str:
        resp = await self.llm_provider.complete(
            prompt=prompt, max_tokens=self.max_tokens, temperature=self.temperature,
        )
        return getattr(resp, "content", "") or ""


__all__ = ["CodingSelector", "FACT_PROMPT", "CHAIN_PROMPT", "_parse_json_object"]
