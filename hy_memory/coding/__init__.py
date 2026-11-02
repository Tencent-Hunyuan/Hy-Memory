# -*- coding: utf-8 -*-
"""
HY Memory - Coding Memory 模块

为生产力 / 编码场景设计的独立记忆链路，双层结构：
- 经验层 CodingMemory（procedural）：可复用的方法，怎么做
- 事实层 WorkingFact（episodic）：一段 trace 的任务、做法与结果
- 独立物理存储（SQLite meta + 两个独立 VDB collection）
- 独立 extractor / 两个 reconciler / store
- 与现有 chat 链路彻底分轨，chat 路径零回归
"""

from .types import (
    CodingMemory,
    CodingWriteError,
    CodingMemoryDraft,
    ReconcileOp,
    WorkingFact,
    WorkingFactDraft,
    FactReconcileOp,
    FactReconcileAction,
    FactStatus,
    FACT_STATUSES,
    MAX_FACT_CHAIN_ENTRIES,
    short_date,
)

__all__ = [
    "CodingMemory",
    "CodingWriteError",
    "CodingMemoryDraft",
    "ReconcileOp",
    "WorkingFact",
    "WorkingFactDraft",
    "FactReconcileOp",
    "FactReconcileAction",
    "FactStatus",
    "FACT_STATUSES",
    "MAX_FACT_CHAIN_ENTRIES",
    "short_date",
]
