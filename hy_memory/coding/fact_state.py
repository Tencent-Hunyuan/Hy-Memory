# -*- coding: utf-8 -*-
"""Coding 事实的结构化状态（task-shaped fact）。

一条 coding 事实存的不再是一段散文，而是这个任务此刻的状态：

    {task, branch, goal, progress, decisions[{subject, current, rejected, evidence}],
     constraints[], status, open[], files[]}

`statement` 由 `render_state()` 从这份状态渲染出来 —— 它同时是检索向量和注入内容，
所以注入时 agent 看到的就是结构化的字段，而不是需要自己再解析的自然语言。

三个只有**结构化**才能做到的事：

1. `decisions[].rejected` —— 试过又放弃的版本必须留着。代码里只会有赢家，后来的
   agent 遇到"被放弃的那版"时若认不出来，会把代码改回去。
2. `decisions[].evidence` —— 已验证 / 仅 git diff / 未验证 的区分跟着结论走，而不是
   在散文里被压成一句"完成了"。
3. 覆盖（supersede）时能把上一版的状态**机械地**带过来（见 `carry_over`），不依赖
   模型记得把旧值挪进 rejected。

状态本身存进 `working_fact.task_state`（JSON），statement 是它的渲染；两者不一致时
以 task_state 为准（statement 只是派生视图）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK_STATUSES = ("in_progress", "completed", "partially_completed", "blocked", "failed")
MAX_DECISIONS = 16
MAX_REJECTED_PER_DECISION = 4
MAX_CONSTRAINTS = 12
MAX_OPEN = 8
MAX_FILES = 20
MAX_RENDER_CHARS = 2400


def _s(value: Any) -> str:
    """压成一行。结构化字段里不该有换行 —— 渲染时靠字段名分行。"""
    return " ".join(str(value or "").split())


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_state(
    raw: Any, *, branch: Optional[str] = None, files: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """把模型给的 state 规范化。缺 task 名就返回 None（调用方退回散文）。"""
    if not isinstance(raw, dict):
        return None
    task = _s(raw.get("task"))
    if not task:
        return None
    decisions: List[Dict[str, Any]] = []
    for d in _as_list(raw.get("decisions")):
        if not isinstance(d, dict) or not _s(d.get("subject")):
            continue
        rejected = [_s(x) for x in _as_list(d.get("rejected")) if _s(x)]
        # 只留最后几条：更早的通常已经被后续版本取代，留着只是占注入预算
        decisions.append({
            "subject": _s(d.get("subject")),
            "current": _s(d.get("current")),
            "rejected": rejected[-MAX_REJECTED_PER_DECISION:],
            "evidence": _s(d.get("evidence")),
        })
    status = _s(raw.get("status")).lower()
    merged_files = list(dict.fromkeys(
        [_s(f) for f in _as_list(raw.get("files")) if _s(f)] + list(files or [])
    ))
    return {
        "task": task,
        "branch": _s(raw.get("branch")) or (branch or ""),
        "goal": _s(raw.get("goal")),
        "progress": _s(raw.get("progress")),
        "decisions": decisions[-MAX_DECISIONS:],
        "constraints": [_s(x) for x in _as_list(raw.get("constraints")) if _s(x)][:MAX_CONSTRAINTS],
        "status": status if status in TASK_STATUSES else "in_progress",
        "open": [_s(x) for x in _as_list(raw.get("open")) if _s(x)][:MAX_OPEN],
        "files": merged_files[:MAX_FILES],
    }


def render_state(state: Dict[str, Any]) -> str:
    """状态 → statement 文本。

    第一行必须是任务名和分支：它们是检索锚点，也是 agent 最先要读到的东西
    （定稿类问题第一步就是 `git checkout` 回那条分支）。
    """
    head = f"Task: {state.get('task') or ''}"
    if state.get("branch"):
        head += f" · branch {state['branch']}"
    head += f" · {state.get('status') or 'in_progress'}"
    lines = [head]
    if state.get("goal"):
        lines.append(f"Goal: {state['goal']}")
    if state.get("progress"):
        lines.append(f"Progress: {state['progress']}")
    decisions = state.get("decisions") or []
    if decisions:
        lines.append("Decisions:")
        for d in decisions:
            row = f"- {d.get('subject')}: {d.get('current') or '(undecided)'}"
            if d.get("evidence"):
                row += f" [{d['evidence']}]"
            if d.get("rejected"):
                row += " (rejected: " + "; ".join(d["rejected"]) + ")"
            lines.append(row)
    if state.get("constraints"):
        lines.append("Constraints: " + "; ".join(state["constraints"]))
    if state.get("open"):
        lines.append("Open: " + "; ".join(state["open"]))
    text = "\n".join(lines)
    if len(text) > MAX_RENDER_CHARS:
        text = text[: MAX_RENDER_CHARS - 1] + "…"
    return text


def parse_state(text: str) -> Optional[Dict[str, Any]]:
    """模型回复 → state dict。容忍 ```json 围栏、前后废话、以及漏转义的反斜杠。

    非法转义是真实会发生的：状态里常抄正则或 Windows 路径（`/\\.svg$/i`、`C:\\dir`），
    模型写出 JSON 时容易漏转义，而 JSON 只认 \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX。
    这里把落单的反斜杠补成两个再试一次 —— 不这么做的话，这类任务会永久退回散文。
    """
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = "\n".join(line for line in body.split("\n") if not line.startswith("```")).strip()
    brace = body[body.find("{"): body.rfind("}") + 1] if "{" in body else ""
    # 从左往右成对吃：合法转义对原样保留，落单的反斜杠补成两个
    repaired = re.sub(
        r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])|\\',
        lambda m: m.group(0) if m.group(1) else "\\\\",
        brace,
    ) if brace else ""
    for candidate in (body, brace, repaired):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _fold(states: List[Optional[Dict[str, Any]]], new: Dict[str, Any]) -> Dict[str, Any]:
    """把历代状态（时间正序）折进新版。

    rejected 的顺序必须**按时间**：历代各自的 current 依次排在前，本次会话自己新放弃
    的后排。逐代顺序调用 carry_over 会得到反序（最新的一代先落进列表），所以这里
    先收集历代 current 再一次性拼接。
    """
    history: Dict[str, List[Dict[str, Any]]] = {}
    constraints: List[str] = []
    goal = ""
    for prev in states:
        if not prev:
            continue
        if not goal:
            goal = prev.get("goal") or ""
        for c in prev.get("constraints") or []:
            if c not in constraints:
                constraints.append(c)
        for d in prev.get("decisions") or []:
            history.setdefault(d.get("subject"), []).append(d)

    for cur in new.get("decisions") or []:
        old_chain = history.get(cur.get("subject")) or []
        if not old_chain:
            continue
        # 历代：先各自 rejected（更早），再这一代的 current（也就是被新版取代的那一版）
        ordered: List[str] = []
        for old in old_chain:
            for v in list(old.get("rejected") or []) + [old.get("current") or ""]:
                if v and _s(v) != _s(cur.get("current")) and v not in ordered:
                    ordered.append(v)
        # 本次会话自己放弃的排在历代之后
        for v in cur.get("rejected") or []:
            if v and v not in ordered:
                ordered.append(v)
        cur["rejected"] = ordered[-MAX_REJECTED_PER_DECISION:]
        if not cur.get("evidence"):
            cur["evidence"] = old_chain[-1].get("evidence") or ""

    for c in new.get("constraints") or []:
        if c not in constraints:
            constraints.append(c)
    new["constraints"] = constraints[:MAX_CONSTRAINTS]
    if not new.get("goal") and goal:
        new["goal"] = goal
    return new


def carry_over(prev: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """覆盖上一版事实时，把它的结论机械地带进新版（单代情况，等价于 _fold 的特例）。"""
    return _fold([prev], new)


def merge_retired(retired_states: List[Optional[Dict[str, Any]]],
                  new: Dict[str, Any]) -> Dict[str, Any]:
    """把历代（按时间正序）的状态一次折进新版。"""
    return _fold(retired_states, new)


def decode(text: str) -> Optional[Dict[str, Any]]:
    """task_state 列（JSON 字符串）→ dict。空串/坏 JSON 都返回 None（当作没有状态）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[coding-fact] task_state is not valid JSON, ignored")
        return None
    return obj if isinstance(obj, dict) else None


__all__ = [
    "TASK_STATUSES", "MAX_RENDER_CHARS",
    "normalize_state", "render_state", "parse_state", "carry_over",
    "decode", "merge_retired",
]
