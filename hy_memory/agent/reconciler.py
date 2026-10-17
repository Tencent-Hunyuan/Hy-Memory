"""
Agent Memory - MemoryReconciler 记忆协调器

在 Lite pipeline 中，LLM 提取完新 facts/identity 后、入库前，
与向量库中的现有记忆做 reconcile：

流程:
  1. （可选）Pre-process: LLM 生成 search queries（MEMORY_ENABLE_SEARCH_QUERY=true，默认关）
  2. 候选搜索: 默认每条新 memory 做向量 + BM25 融合检索
     （向量扩池 + 独立 keyword + additive(sem/bm25/max(tag,entity))，min_score=0.4）；
     开启 search query 时额外用扩写 query 做向量搜索
  3. 一次 LLM 调用（Reconcile），输入新记忆 + 候选旧记忆
  4. 返回操作指令列表（ADD / DELETE，按顺序执行）
  5. 调用方按指令执行入库操作并更新旧节点状态

操作 Schema（两种 op 自由组合表达三种语义）:
  ADD    → 新增节点
           supersedes=[]         → 场景 A：全新信息（与任何旧记忆无关）
           supersedes=[id, ...]  → 场景 B：矛盾/取代（旧节点不再成立，进演化链）
  DELETE → 逻辑删除旧节点（status=SHADOW, is_latest=False，不进演化链）
           场景 C：合并吸收 — 配合 ADD(supersedes=[]) 使用，表达
                   "这条 ADD 吸收了某几条旧记忆的内容，旧记忆不再召回"

  例子：
    场景 B 矛盾：ADD(content="喝茶", supersedes=[id_coffee])
    场景 C 合并：ADD(content="有 PI 血统并在音乐中运用", supersedes=[]) + DELETE(id_heritage)
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .llm_provider import (
    LLMProvider,
    _content_fingerprint,
    _log_llm_content_enabled,
)
from ..config import MemoryConfig

logger = logging.getLogger(__name__)


def is_canonical_state_node(node: Any) -> bool:
    """Canonical state projections are owned by System2's exact-key lifecycle."""
    return (getattr(node, "custom", None) or {}).get("memory_kind") == "canonical_state"


def _log_ops_detail(ops: List["ReconcileOp"]) -> None:
    """以单条 INFO（JSON list）输出 reconcile 的逐条结果，便于线上 debug。"""
    if not ops:
        return
    view = [
        {
            "op": o.op,
            "memory_id": o.memory_id or None,
            "content": o.content,
            "tags": list(o.tags or []),
            "topic_paths": list(o.topic_paths or []),
            "event_facet": dict(o.event_facet or {}),
            "topic_assignments": list(o.topic_assignments or []),
            "structure_references": list(o.structure_references or []),
            "related": list(o.related or []),
            "reason": o.supersede_reason or o.reason or "",
        }
        for o in ops
    ]
    logger.info(
        f"[reconciler] ops detail: {json.dumps(view, ensure_ascii=False, default=str)}"
    )


# ================================================================
# 操作指令
# ================================================================


@dataclass
class ReconcileOp:
    """
    单条操作指令（v2 flat 格式）。

    op = "ADD"：纯新增节点，信息完全新颖，不引用任何现有记忆。

    op = "SUPERSEDE"：状态演变/矛盾链。新记忆取代旧记忆成为链头部。
      - memory_id 指向被取代的旧节点
      - content 仅描述新/变更的信息（旧节点自动保留在链中）
      - supersede_reason 描述矛盾原因
      - 执行：旧节点 → SHADOW + is_latest=False；新节点 supersedes=[old_id]（进演化链）

    op = "UPDATE"：合并与精炼。新记忆与旧记忆描述同一主题且不矛盾，合并为高密度节点。
      - memory_id 指向被更新的旧节点
      - content 是合并后的完整文本
      - 执行：旧节点 → SHADOW + is_latest=False；新节点独立创建（不进演化链，无 supersedes）
      - 旧节点作为历史保留在 VDB 中（status=SHADOW），但不形成链关系
    """

    op: str = "ADD"
    # --- 共用字段 ---
    content: Optional[str] = None  # 最终存储内容
    layer: Optional[str] = None  # "L2_FACT" / "L4_IDENTITY"
    owner: Optional[str] = None  # 'user' / 'agent'（仅 L2_FACT 有意义）
    tags: List[str] = field(default_factory=list)  # 1-3 keywords；专名保留大小写
    tag_ners: List[str] = field(default_factory=list)  # 与 tags 等长的 NER label
    # 写入时显式组织结构。topic_paths 从宽到窄；event_facet 只描述事实语义，
    # 不携带 future query 的问句类型或路由提示。
    topic_paths: List[List[str]] = field(default_factory=list)
    event_facet: Dict[str, Any] = field(default_factory=dict)
    topic_assignments: List[Dict[str, Any]] = field(default_factory=list)
    structure_references: List[Dict[str, str]] = field(default_factory=list)
    topic_paths_provided: bool = False
    event_facet_provided: bool = False
    topic_assignments_provided: bool = False
    speculate: Optional[str] = None  # 隐性偏好推导（仅 identity）
    # --- SUPERSEDE / UPDATE 字段 ---
    memory_id: Optional[str] = None  # 目标旧节点 ID（单节点；多节点链时为链上最新的那个）
    memory_ids: List[str] = field(default_factory=list)  # SUPERSEDE 多节点链：有序（旧→新）的现有节点 ID 列表；用于把多条同维度旧记忆折叠进同一条演化链
    supersede_reason: str = ""  # SUPERSEDE 的矛盾描述
    # --- 关系图（pro 模式，见 docs/pro_graph_design.md）---
    # LLM 标注的与现有记忆的关系：[{"target_id": <existing memory_id>, "relation_type": ...}]
    # relation_type ∈ causes / elaborates / contrasts / same_series。
    # reconcile 本来就同时摊开了 existing 和 new memories，让它多输出这个字段只花输出 token，
    # 不增加 LLM 调用；而 causes / contrasts 这类边的两端余弦往往很低，向量检索永远发现不了。
    related: List[Dict[str, str]] = field(default_factory=list)
    # --- 通用 ---
    reason: str = ""


@dataclass
class ReconcileResult:
    """Reconcile 结果"""

    ops: List[ReconcileOp] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    # True = reconcile 失败后走了「全部 ADD」兜底（见 _fallback_add_ops）。
    # 结果仍然可用（success=True），但没有做过去重/演化判定，可能引入重复。
    degraded: bool = False
    degraded_reason: Optional[str] = None
    # reconcile 链路 LLM token 消耗（search-query + reconcile 两次 LLM 调用之和）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 候选池的相似度快照：{新记忆 content: [(existing_node_id, score), ...]}
    #
    # reconcile 本来就为每条新记忆做了 hybrid 检索并算出融合分数，用完就丢。写侧把它
    # 存成 `semantic_neighbor` 边是**零额外计算**的（见 docs/pro_graph_design.md §2.2）。
    # 用 content 作 key 而不是 node_id：新记忆此刻还没入库、没有 id。
    candidate_scores: Dict[str, List[Tuple[str, float]]] = field(default_factory=dict)
    # reconcile 候选检索已经为每条新 memory 算过 embedding。把原始文本对应向量交回
    # writer，ADD-all / degraded ADD 可直接落库，避免同一事实再次请求 embedding。
    # 仅按完全相同 content 复用；LLM 改写后的 UPDATE/SUPERSEDE 会重新计算，防止错配。
    new_memory_embeddings: Dict[str, List[float]] = field(default_factory=dict)


# ================================================================
# Prompts
# ================================================================

SEARCH_QUERY_PROMPT = """You are a search query generator. Given a list of newly extracted memories, generate a set of short search queries that can be used to find related existing memories in a vector database.

The goal is to maximize recall — find existing memories that are semantically related to the new memories, even if the wording is very different.

## New memories:
{new_memories}

## Instructions

Generate search queries that cover:
- Key topics, entities, and themes mentioned in the memories
- Rephrased or abstracted versions of the core concepts
- Related concepts that might exist in the user's memory store

Output a JSON array of query strings (5-15 queries, short and focused):

["query1", "query2", "query3", ...]

Output JSON array only, no other text."""

RECONCILE_PROMPT = """You are a memory management system.

Your task is to merge new memories into an existing memory base while keeping it:

* lossless
* non-contradictory
* compact
* highly retrievable

---

# Operations

## ADD

Use when the information is entirely new.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

Use when:

* the new memory is compatible with an existing one
* they describe the same topic
* merging improves completeness or retrievability

Rules:

* merge losslessly
* preserve all useful information
* do NOT create duplicates

### Same occurrence vs. another occurrence

Use **UPDATE** only when the new memory adds detail to the same real-world
occurrence or durable fact already recorded.

If the same person does the same kind of activity/event again at another time
(another trip, game, injury, purchase, health scare, etc.), keep the occurrence
as a separate **ADD**. Preserve its own date and details. When an existing memory
is clearly another occurrence in that same concrete series, link it with
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

Do not merge separate dated occurrences into one long count/timeline memory.
The retrieval layer builds timelines from the linked L2 memories.

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

Use ONLY when:

* the new memory contradicts an existing memory
* both cannot be true at the same time
* the old memory would now give the wrong current-state answer

Examples:

* city changed
* employer changed
* relationship status changed

Do NOT use for:

* added detail
* compatible preferences
* wording differences
* semantic equivalence

Rules:

* content must contain ONLY the new state
* do NOT copy unchanged information
* the system keeps the old node and links it onto the evolution chain automatically; you don't need to handle history

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### Folding several existing memories into one chain

If TWO OR MORE existing memories track the SAME dimension and the new memory is
the latest change on that dimension, you may fold them into a single evolution
chain. Use `memory_ids` (an ordered list, OLDEST → NEWEST) instead of a single
`memory_id`:

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<only the newest state>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

The listed memories are chained oldest → newest and the new memory becomes the
chain head. `content` still describes ONLY the newest state — never copy the
older memories' text into it; they remain queryable in the chain history.

---

# Rules

* Preserve all meaningful information.
* Skip exact duplicates.
* Prefer merging related fragments.
* Separate dated occurrences stay separate ADDs; use `same_series` only for the
  same subject and same concrete activity/event type.
* Keep each memory focused on one coherent topic.
* Do not invent chronology.
* `memory_at: null` means unknown time.
* **NEVER merge or supersede across different subjects.** The subject is whoever
  the content is about: "The user ..." vs "John ..." vs "Mary ...". Memories about
  different people are independent facts even when the sentence is otherwise
  identical — "The user likes coffee" and "John likes coffee" are two separate
  memories, never an UPDATE or a SUPERSEDE of each other.
* `owner` (`user`/`agent`): copy it from the memory being written. NEVER merge or supersede across different owners — a user's fact and the assistant's fact are distinct even on the same topic.
* A SUPERSEDE either targets ONE memory (`memory_id`) or folds several memories on the same dimension into one chain (`memory_ids`, oldest→newest). An UPDATE targets exactly ONE memory.
* Never touch the same existing memory from more than one op.
* Output language matches input memory language.

---

# Optional: `related`

On any op you MAY add a `related` array recording how the memory being written
relates to an EXISTING memory. This does not change what gets stored — it only
records a link.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Only these four relations are allowed:

* `causes` — the two memories are in a cause/effect relationship
  (e.g. "started running daily" → "knee started hurting")
* `elaborates` — adds detail to, or is a later development of, the existing memory
* `contrasts` — they are in tension but BOTH still true (if one makes the other
  false, that is a SUPERSEDE, not a `contrasts` link)
* `same_series` — separate dated occurrences of the same concrete activity or
  event type for the same subject (for example, two different skiing trips)

Rules:

* Only link to memories that appear in "Existing memories" — never invent an id.
* Only emit a link when the relationship is **explicit and specific**. Two memories
  merely being about the same broad topic is NOT a relation — leave `related` out.
* `same_series` requires the same subject and a concrete repeated action/event;
  shared broad tags such as health, travel, sport, food, or work are insufficient.
* Omit the field entirely when there is nothing to link. Zero links is the normal case.
* Never use `related` as a substitute for UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Input Data
###################

Current date: {current_date}

## Existing memories

{existing_memories}

## New memories

{new_memories}

---

# Output

Return ONLY a JSON array inside a fenced ```json code block.

If no changes are needed:

```json
[]
```

Now produce the JSON array."""

RECONCILE_STRUCTURE_METADATA_PROMPT_EN = """

Additional structural output contract:

For every ADD, UPDATE, or SUPERSEDE result, preserve or refine `topic_paths`,
`event_facet`, and `topic_assignments`. `event_facet` contains only `subject`,
`participants`, `action`, `objects`, `locations`, and `state`.
`topic_assignments` contains subject-specific `{subject, role, path}` entries;
each path segment is `{name, type}` and type is `topic|facet|series|episode`.
Types follow `topic -> facet -> series -> episode`; levels may be skipped but
types may not repeat or move backward.
Use Series only for repeated occurrences of one concrete activity/state line and
Episode only for one bounded real-world occurrence. Never infer these types from
depth, a broad shared tag, or unrelated co-occurrence. When an Existing memory
already has a semantically equivalent subject-specific path, reuse its exact node
names and types instead of creating a synonym fork. To request exact reuse, emit
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`. The referenced ID must appear in
Existing memories. `through` means copy that existing path prefix through the
named structural type; an explicitly authored deeper suffix remains new. Reuse
is forbidden when the subject, concrete activity/state line, or Episode boundary
differs. These fields describe the memory, never a future query form such as
"when", "how many", or "who".

Series is a reconcile-time relation: establish it only when the new memory and
at least one independent Existing memory are distinct occurrences of the same
concrete activity/state line. Multiple propositions from the current write do
not prove repetition.
"""


def append_reconcile_structure_prompt(
    prompt: str, *, enabled: bool, language: str,
) -> str:
    """Append the structure contract only for an explicit experiment config."""
    if not enabled:
        return prompt
    if language == "zh":
        from .prompts_zh import RECONCILE_STRUCTURE_METADATA_PROMPT_ZH
        return prompt + RECONCILE_STRUCTURE_METADATA_PROMPT_ZH
    return prompt + RECONCILE_STRUCTURE_METADATA_PROMPT_EN


def build_reconcile_prompt(
    *,
    new_memories: List[str],
    new_memories_block: str,
    existing_memories_block: str,
    current_date: str,
    few_shot_enabled: bool = False,
    structure_metadata_enabled: bool = False,
) -> str:
    """Build the language-matched reconcile prompt used by the write path."""
    from ..utils.lang_detect import is_chinese

    if is_chinese("\n".join(new_memories)):
        from .prompts_zh import RECONCILE_FEW_SHOT_ZH, RECONCILE_PROMPT_ZH

        few_shot = ("\n" + RECONCILE_FEW_SHOT_ZH + "\n") if few_shot_enabled else ""
        prompt = RECONCILE_PROMPT_ZH.format(
            current_date=current_date,
            new_memories=new_memories_block,
            existing_memories=existing_memories_block,
            few_shot_section=few_shot,
        )
        return append_reconcile_structure_prompt(
            prompt,
            enabled=structure_metadata_enabled,
            language="zh",
        )

    few_shot = ("\n" + RECONCILE_FEW_SHOT_EN + "\n") if few_shot_enabled else ""
    prompt = RECONCILE_PROMPT.format(
        current_date=current_date,
        new_memories=new_memories_block,
        existing_memories=existing_memories_block,
        few_shot_section=few_shot,
    )
    return append_reconcile_structure_prompt(
        prompt,
        enabled=structure_metadata_enabled,
        language="en",
    )


# Reconcile few-shot examples (injected into {few_shot_section} only when
# few_shot_enabled). Each shows the existing/new input and the expected ops,
# with a one-line takeaway. IDs are shortened for readability.
RECONCILE_FEW_SHOT_EN = """
###################
Examples
###################

## Example 1 — One new memory: part conflicts with an existing chain, part is unrelated

Existing memories:
[
  {"memory_id": "m1", "content": "The user works as a product manager at Stripe.", "owner": "user", "history_versions": [{"content": "The user works as a product manager at a fintech startup."}]}
]
New memories:
[
  {"content": "The user has left Stripe and is now a product lead at Notion, and has recently taken up pottery on weekends.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "The user works as a product lead at Notion.", "supersede_reason": "employer changed from Stripe to Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "The user has recently taken up pottery on weekends.", "owner": "user", "tags": ["hobby"]}
]
```
A SINGLE new memory carries two things: the job change (same dimension as the existing employment chain m1, which already has history → SUPERSEDE, becomes the new chain head) and an unrelated detail (pottery → ADD). Split the one memory into the right op per part — the new head describes ONLY the employment state, never the pottery.

## Example 2 — Fold two unlinked existing memories + the new change into one 3-node chain

Existing memories:
[
  {"memory_id": "a1", "content": "The user is learning to play the guitar.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "The user can now play a few full songs on the guitar.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
New memories:
[
  {"content": "The user now performs guitar at local open-mic nights.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "The user performs guitar at local open-mic nights.", "supersede_reason": "guitar skill progressed: learning → playing songs → performing live", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 and a2 were never linked, but they track the same dimension (guitar progress) and the new memory is the latest step. Fold them with `memory_ids` ordered oldest→newest; they become a 3-node chain (a1 ← a2 ← new head). The head `content` describes ONLY the current state — it does not restate "learning" or "a few songs".

## Example 3 — New head must NOT copy the old memory's content

Existing memories:
[
  {"memory_id": "c1", "content": "The user lives in a rented one-bedroom apartment in Shibuya, Tokyo, pays ¥180,000/month, loves the 5-minute walk to the station, but complains the place is noisy at night and has no parking.", "owner": "user"}
]
New memories:
[
  {"content": "The user just bought and moved into a three-bedroom house in the suburbs of Yokohama with a garden and a two-car garage.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "The user bought and moved into a three-bedroom house in suburban Yokohama, with a garden and a two-car garage.", "supersede_reason": "residence changed from a rented Shibuya apartment to an owned Yokohama house", "owner": "user", "tags": ["housing"]}
]
```
The new memory SUPERSEDEs c1 and becomes the chain head. The head `content` describes ONLY the new Yokohama house. Do NOT drag the old apartment's distinctive details (Shibuya, ¥180,000 rent, the station walk, the noise/parking complaints) into the new node — those clearly belong to the old residence, not the new one. They stay in chain history and remain retrievable; copying them would make the head self-contradictory (renting in Shibuya AND owning in Yokohama at once).

## Example 4 — Different subjects are never merged, however similar the wording

Existing memories:
[
  {"memory_id": "d1", "content": "The user drinks black coffee every morning.", "owner": "user"}
]
New memories:
[
  {"content": "John drinks black coffee every morning.", "owner": "user"},
  {"content": "The user now drinks black coffee twice a day, morning and after lunch.", "owner": "user"}
]

Output:
```json
[
  {"op": "ADD", "content": "John drinks black coffee every morning.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "The user drinks black coffee twice a day, in the morning and after lunch.", "owner": "user", "tags": ["food"]}
]
```
The two new memories are near-identical in wording but have DIFFERENT subjects. "John ..." is about another person, so it is a plain ADD — never an UPDATE or SUPERSEDE of d1, even though d1 says almost the same thing about the user. Only the memory whose subject is also "The user" may merge with d1.

## Example 5 — Recurring event: bump count and keep a timeline (UPDATE, not ADD)

Existing memories:
[
  {"memory_id": "i1", "content": "John injured his ankle last season and needed time off plus physical therapy.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
New memories:
[
  {"content": "John twisted his ankle again during a recent game.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Output:
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John has injured his ankle two times: first last season (around the 2022–2023 season), needing time off and physical therapy; second during a game around 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
Same event type happening again → UPDATE the old memory. State the **count** and list occurrences with times when known. Do NOT ADD a second "John injured his ankle" fact, and do NOT SUPERSEDE (the first injury did not stop being true).
"""


# ================================================================
# MemoryReconciler
# ================================================================


class MemoryReconciler:
    """
    记忆协调器

    在 lite pipeline 中，提取完新 facts/identity 后，
    与向量库现有记忆做 reconcile，再决定入库操作。
    """

    # 每条新 memory hybrid 融合后保留的候选数
    SEARCH_TOPK = 20
    # hybrid 向量扩池倍数（limit × 3）
    VEC_POOL_MULTIPLIER = 3
    # 融合分阈值（additive；无 BM25 时 sem 不打折）
    HYBRID_MIN_SCORE = 0.4
    # search query 扩写路向量 topk（仅 MEMORY_ENABLE_SEARCH_QUERY=true）
    QUERY_SEARCH_TOPK = 20
    SEARCH_THRESHOLD = 0.3
    # 链条合并后最终保留的候选数（每路）
    FINAL_TOPK = 10
    # max_tokens for reconcile LLM call（基础值 + 动态扩展）
    # 每条 ADD 输出大约 200-300 tokens（content + supersedes + reason）
    # 总上限 8000 tokens，防止单次调用失控
    MAX_TOKENS = 2000
    PER_OP_TOKENS = 300
    BASE_OVERHEAD_TOKENS = 500
    MAX_TOKENS_UPPER_BOUND = 8000

    def __init__(
        self,
        config: MemoryConfig,
        llm_provider: Optional[LLMProvider] = None,
    ):
        self.config = config
        self._llm: Optional[LLMProvider] = llm_provider
        # 消融开关：关闭时只用新 memory 直接搜索，不生成 search queries
        self._enable_search_query = self._read_enable_search_query()

    @staticmethod
    def _read_enable_search_query() -> bool:
        v = os.getenv("MEMORY_ENABLE_SEARCH_QUERY", "false").lower().strip()
        return v not in ("false", "0", "no", "off")

    def _get_llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = LLMProvider(self.config)
        return self._llm

    async def _generate_search_queries(self, new_memories: List[str]) -> tuple:
        """
        Pre-process: 从全部新 memory 中提取 search queries，
        用于补充向量搜索的召回能力。

        Returns:
            (queries: List[str], llm_response: Optional[LLMResponse])
        """
        mem_lines = "\n".join(f"{i+1}. {m}" for i, m in enumerate(new_memories))

        # Select prompt based on input language
        from ..utils.lang_detect import is_chinese

        if is_chinese("\n".join(new_memories)):
            from .prompts_zh import SEARCH_QUERY_PROMPT_ZH

            prompt = SEARCH_QUERY_PROMPT_ZH.format(new_memories=mem_lines)
        else:
            prompt = SEARCH_QUERY_PROMPT.format(new_memories=mem_lines)

        try:
            llm = self._get_llm()
            response = await llm.complete(
                prompt=prompt,
                max_tokens=500,
                audit_stage="reconcile",
            )
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            queries = json.loads(text)
            if isinstance(queries, list):
                queries = [q for q in queries if isinstance(q, str) and q.strip()]
                logger.debug(
                    f"[reconciler] generated {len(queries)} search queries: {queries}"
                )
                return queries, response
        except Exception as e:
            logger.warning(f"[reconciler] search query generation failed: {e}")

        return [], None

    @staticmethod
    def _fallback_add_ops(
        new_memories: List[str],
        new_memories_with_meta: Optional[List[Dict]] = None,
    ) -> List[ReconcileOp]:
        """reconcile 彻底失败时的兜底：把抽取结果**原样全部 ADD**。

        为什么必须有这个兜底：reconcile 只是决定「新事实和旧记忆怎么合」。它挂了（网关 500、
        JSON 解析不出）不代表抽取结果没价值 —— 此前的行为是把一整批完好的事实**全部丢弃**，
        线上实测因此丢掉过整块内容（含评测题的正确答案）。宁可留重复也不要丢内容：
        重复还能被后续 dedup / reconcile 收拾，丢了就永远找不回来。
        """
        metas = new_memories_with_meta or []
        by_content = {
            (m.get("content") or ""): m for m in metas if isinstance(m, dict)
        }
        ops: List[ReconcileOp] = []
        for text in new_memories:
            if not text:
                continue
            meta = by_content.get(text) or {}
            ops.append(ReconcileOp(
                op="ADD",
                content=text,
                layer=meta.get("layer"),
                tags=meta.get("tags") or [],
                owner=meta.get("owner"),
                topic_paths=meta.get("topic_paths") or [],
                event_facet=meta.get("event_facet") or {},
                topic_assignments=meta.get("topic_assignments") or [],
                topic_paths_provided=bool(
                    meta.get("topic_paths_provided", "topic_paths" in meta)
                ),
                event_facet_provided=bool(
                    meta.get("event_facet_provided", "event_facet" in meta)
                ),
                topic_assignments_provided=bool(meta.get(
                    "topic_assignments_provided", "topic_assignments" in meta,
                )),
                reason="reconcile unavailable → stored as-is (degraded)",
            ))
        return ops

    async def reconcile(
        self,
        new_memories: List[str],
        user_id: str,
        agent_id: str,
        vector_store,  # VectorStoreBase
        embed_service,  # EmbedService
        layers=None,  # Optional[List[MemoryLayer]]
        cache=None,  # Optional[CacheBase], 用于写 pipeline_logs
        request_id: str = "",
        current_time: str = "",
        new_memories_with_meta: Optional[List[Dict]] = None,  # 含 tags 的完整 meta
        session_id: str = "",  # 隔离维度，透传到 pipeline_logs
        graph_store=None,  # Optional GraphStore：Entity MENTIONS boost
        new_memory_embeddings: Optional[Dict[str, List[float]]] = None,
        source: str = "default",
    ) -> ReconcileResult:
        """
        对 new_memories 做 reconcile。

        Args:
            new_memories: 新记忆内容列表（content only，用于 embed 搜索）
            new_memories_with_meta: 可选，含 tags 的完整 meta
        """
        if not new_memories:
            return ReconcileResult(ops=[], success=True)

        from ..models.memory import MemoryLayer, MemoryStatus, MemoryNode
        import asyncio

        if layers is None:
            layers = [MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY]

        try:
            import time as _time

            # ── Step 1: Search Query 生成（可消融）──
            search_queries: List[str] = []
            sq_response = None
            t_sq = _time.perf_counter()

            if self._enable_search_query:
                search_queries, sq_response = await self._generate_search_queries(
                    new_memories
                )
            sq_elapsed = (_time.perf_counter() - t_sq) * 1000

            # token 累计：search-query 这次 LLM 调用的消耗（reconcile 调用稍后追加）
            _tok_pt = sq_response.prompt_tokens if sq_response else 0
            _tok_ct = sq_response.completion_tokens if sq_response else 0
            _tok_total = sq_response.tokens_used if sq_response else 0

            # 写 SEARCH_QUERY pipeline log
            if cache and request_id:
                mem_lines = "\n".join(f"{i+1}. {m}" for i, m in enumerate(new_memories))
                if self._enable_search_query:
                    from ..utils.lang_detect import is_chinese as _is_zh

                    if _is_zh("\n".join(new_memories)):
                        from .prompts_zh import SEARCH_QUERY_PROMPT_ZH

                        sq_prompt = SEARCH_QUERY_PROMPT_ZH.format(
                            new_memories=mem_lines
                        )
                    else:
                        sq_prompt = SEARCH_QUERY_PROMPT.format(new_memories=mem_lines)
                else:
                    sq_prompt = "[search query disabled — hybrid retrieval per new memory]"
                try:
                    await cache.store_pipeline_log(
                        request_id=request_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        step="SEARCH_QUERY",
                        prompt=sq_prompt,
                        response=json.dumps(search_queries, ensure_ascii=False),
                        parsed=json.dumps(search_queries, ensure_ascii=False),
                        elapsed_ms=sq_elapsed,
                        prompt_tokens=sq_response.prompt_tokens if sq_response else 0,
                        completion_tokens=(
                            sq_response.completion_tokens if sq_response else 0
                        ),
                        total_tokens=sq_response.tokens_used if sq_response else 0,
                    )
                except Exception as e:
                    logger.warning(f"[reconciler] store SEARCH_QUERY log failed: {e}")

            # ── Step 2: 候选搜索，合并候选池 ──
            candidate_map: Dict[str, MemoryNode] = {}
            candidate_scores: Dict[str, float] = {}

            supplied_embeddings = dict(new_memory_embeddings or {})
            mem_embeddings: List[Optional[List[float]]] = [
                supplied_embeddings.get(text) for text in new_memories
            ]
            missing_mem_indices = [
                i for i, emb in enumerate(mem_embeddings) if not emb
            ]
            texts_to_embed = [new_memories[i] for i in missing_mem_indices]
            texts_to_embed.extend(search_queries)
            computed_embeddings = (
                await embed_service.embed_batch(texts_to_embed)
                if texts_to_embed else []
            )
            for i, emb in zip(
                missing_mem_indices,
                computed_embeddings[: len(missing_mem_indices)],
            ):
                mem_embeddings[i] = emb
            query_embeddings = computed_embeddings[len(missing_mem_indices):]

            def _merge_hits(hits: List[Dict[str, Any]], source: str) -> None:
                logger.debug(f"[reconciler] search {source}: {len(hits)} results")
                for r in hits:
                    node = r.get("node")
                    score = float(r.get("score", 0))
                    if not node:
                        continue
                    # Canonical states are materialized and versioned by nightly
                    # evoling using an exact state_key.  Letting the free-form S1
                    # reconciler mutate them turns a stable state back into an
                    # event summary and breaks deterministic lifecycle handling.
                    if is_canonical_state_node(node):
                        continue
                    nid = node.node_id
                    if nid not in candidate_map:
                        candidate_map[nid] = node
                        candidate_scores[nid] = score
                    else:
                        candidate_scores[nid] = max(candidate_scores[nid], score)

            from ..pipelines._retrieval.reconcile_retrieval import (
                ReconcileHybridRetriever,
                ReconcileRetrievalConfig,
            )

            hybrid = ReconcileHybridRetriever(
                ReconcileRetrievalConfig.from_env(
                    limit=self.SEARCH_TOPK,
                    vec_pool_multiplier=self.VEC_POOL_MULTIPLIER,
                    min_score=self.HYBRID_MIN_SCORE,
                )
            )

            new_memory_embeddings = {
                text: emb
                for text, emb in zip(new_memories, mem_embeddings)
                if text and emb
            }

            hybrid_indices = [
                i for i in range(len(new_memories))
                if i < len(mem_embeddings) and mem_embeddings[i]
            ]

            query_indices = []
            if self._enable_search_query and search_queries:
                query_indices = [
                    i for i in range(len(search_queries))
                    if i < len(query_embeddings)
                ]

            _t_candidate_search = _time.perf_counter()
            hybrid_result = []
            query_result = []
            tasks = []
            if hybrid_indices:
                tasks.append(hybrid.search_candidates_batch(
                    [new_memories[i] for i in hybrid_indices],
                    [mem_embeddings[i] for i in hybrid_indices],
                    vector_store=vector_store,
                    user_id=user_id,
                    agent_id=agent_id,
                    layers=layers,
                    embed_service=embed_service,
                    graph_store=graph_store,
                    include_sources=[source],
                ))
            if query_indices:
                tasks.append(vector_store.search_batch([
                    {
                        "query_embedding": query_embeddings[i],
                        "user_id": user_id,
                        "agent_ids": None,
                        "layers": layers,
                        "limit": self.QUERY_SEARCH_TOPK,
                        "score_threshold": self.SEARCH_THRESHOLD,
                        "status_filter": [
                            MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED,
                        ],
                        "only_latest": False,
                        "include_sources": [source],
                    }
                    for i in query_indices
                ]))
            if tasks:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)
                cursor = 0
                if hybrid_indices:
                    hybrid_result = task_results[cursor]
                    cursor += 1
                if query_indices:
                    query_result = task_results[cursor]
            _candidate_search_ms = round(
                (_time.perf_counter() - _t_candidate_search) * 1000, 2,
            )
            # 关系图用：记下"每条新记忆的 hybrid 候选及其分数"。
            # 这些分数本来算完就丢，写侧拿它建 semantic_neighbor 边是零额外计算的。
            # 用 content 作 key —— 新记忆此刻还没入库，没有 node_id。
            per_memory_candidates: Dict[str, List[Tuple[str, float]]] = {}

            if isinstance(hybrid_result, Exception):
                logger.warning("[reconciler] hybrid batch failed: %s", hybrid_result)
                hybrid_result = []
            if isinstance(query_result, Exception):
                logger.warning("[reconciler] query batch failed: %s", query_result)
                query_result = []

            for source_index, result in zip(hybrid_indices, hybrid_result):
                _merge_hits(result, f"memory_hybrid[{source_index}]")
                per_memory_candidates[new_memories[source_index]] = [
                            (r["node"].node_id, float(r.get("score", 0.0)))
                            for r in (result or [])
                            if r.get("node") is not None
                            and not is_canonical_state_node(r["node"])
                ]
            for source_index, result in zip(query_indices, query_result):
                _merge_hits(
                    [
                        {"node": r.get("node"), "score": r.get("score", 0)}
                        for r in result
                    ],
                    f"query[{source_index}]",
                )

            logger.debug(
                f"[reconciler] search: {len(new_memories)} memories (hybrid) + "
                f"{len(search_queries)} queries → {len(candidate_map)} raw candidates"
            )

            # ── Step 2.5: 按演化链合并（复用 reader 侧 _trace_full_chain）──
            # 召回池可能含 SUPERSEDED 旧节点（链身）。对每个候选节点双向追溯
            # （supersedes / superseded_by）补全整条链，以链头（is_latest）为代表
            # 去重：同一条链的多个命中（链头+链身）合并为一个候选，链头作代表，
            # 链头分数取该链所有命中的最高分。
            # 注：祖先节点仅用于去重/避免重复候选，**不进 prompt**（见 Step 4，
            # supersede 时只暴露 head 的 id+content，history 不提供，防止 LLM 误操作）。
            from ..pipelines._retrieval.evolution import trace_full_chains

            chain_head_node: Dict[str, MemoryNode] = {}   # head_id → head node
            chain_best_score: Dict[str, float] = {}       # head_id → 链上最高命中分
            node_to_head: Dict[str, str] = {}             # 任一命中 node_id → 其链头 id
            chain_members: Dict[str, List[str]] = {}      # head_id → 整条链 node_id（head+bodies）

            _t_chain_expand = _time.perf_counter()
            try:
                traced_chains = await trace_full_chains(
                    vector_store, list(candidate_map.values()),
                )
            except Exception as e:
                logger.warning("[reconciler] batch chain trace failed: %s", e)
                traced_chains = {}
            for nid, node in candidate_map.items():
                if nid in node_to_head:
                    continue  # 已被某条已展开的链覆盖
                chain = traced_chains.get(nid) or [node]
                head = chain[0]  # _trace_full_chain 保证 [0] 为链头（is_latest 优先）
                head_id = head.node_id
                chain_head_node.setdefault(head_id, head)
                chain_members[head_id] = [cn.node_id for cn in chain]
                # 该链上所有“恰好被召回”的节点，分数并入链头候选分
                for cn in chain:
                    node_to_head[cn.node_id] = head_id
                    if cn.node_id in candidate_scores:
                        chain_best_score[head_id] = max(
                            chain_best_score.get(head_id, 0.0),
                            candidate_scores[cn.node_id],
                        )

            # 按链头候选分排序，取 top（留足余量）
            sorted_chains = sorted(
                chain_head_node.keys(),
                key=lambda hid: chain_best_score.get(hid, 0.0),
                reverse=True,
            )[: self.FINAL_TOPK * 2]

            # 最终候选：以链头为代表（prompt 只展示 head）
            final_candidates: Dict[str, MemoryNode] = {}
            for head_id in sorted_chains:
                final_candidates[head_id] = chain_head_node[head_id]
                candidate_scores[head_id] = chain_best_score.get(head_id, 0.0)

            candidate_map = final_candidates
            _chain_expand_ms = round(
                (_time.perf_counter() - _t_chain_expand) * 1000, 2,
            )
            logger.debug(
                f"[reconciler] after chain merge: {len(candidate_map)} chain groups "
                f"(traced from {len(node_to_head)} recalled chain nodes)"
            )

            # ── Step 2.7: 候选构成埋点 ──
            # 候选检索已放宽到 user 维度（不再按 agent 收窄），候选池里会混入其他
            # agent 的记忆。在 topk 不变的前提下，多 agent 用户的本 agent 候选
            # 可能被挤出去（"稀释"）。这里把构成落进 pipeline log，上线后可直接
            # 从日志确认稀释是否真的发生，而不必等业务反馈。
            #
            # 单 agent 用户 cross_agent_count 恒为 0 —— 放宽前后捞回的是同一批。
            try:
                _cand_own_agent = 0
                _cand_cross_agent = 0
                _cand_owner_user = 0
                _cand_owner_agent = 0
                _cross_agent_ids: set = set()
                for _n in candidate_map.values():
                    _naid = getattr(_n, "agent_id", "") or ""
                    if _naid and _naid != agent_id:
                        _cand_cross_agent += 1
                        _cross_agent_ids.add(_naid)
                    else:
                        _cand_own_agent += 1
                    if (getattr(_n, "owner", None) or "user") == "agent":
                        _cand_owner_agent += 1
                    else:
                        _cand_owner_user += 1

                _cand_parsed = {
                    "candidate_count": len(candidate_map),
                    "own_agent_count": _cand_own_agent,
                    "cross_agent_count": _cand_cross_agent,
                    "cross_agent_ids": sorted(_cross_agent_ids)[:20],
                    "owner_user_count": _cand_owner_user,
                    "owner_agent_count": _cand_owner_agent,
                    "recalled_chain_nodes": len(node_to_head),
                    "final_topk": self.FINAL_TOPK * 2,
                    # 候选池打满时才可能发生稀释；未打满说明没有竞争
                    "topk_saturated": len(candidate_map) >= self.FINAL_TOPK * 2,
                    "scope": "user",
                    "new_memory_count": len(new_memories),
                    "hybrid_query_count": len(hybrid_indices),
                    "query_query_count": len(query_indices),
                    "dense_batch_count": int(bool(hybrid_indices))
                    + int(bool(query_indices)),
                    "keyword_batch_count": int(bool(hybrid_indices)),
                    "candidate_search_ms": _candidate_search_ms,
                    "chain_expand_ms": _chain_expand_ms,
                    "vector_store_local": bool(
                        getattr(vector_store, "_is_local", False)
                    ),
                }
                logger.info(
                    f"[reconciler] candidates={len(candidate_map)} "
                    f"own_agent={_cand_own_agent} cross_agent={_cand_cross_agent} "
                    f"owner_user={_cand_owner_user} owner_agent={_cand_owner_agent} "
                    f"saturated={_cand_parsed['topk_saturated']}"
                )
                if cache:
                    await cache.store_pipeline_log(
                        request_id=request_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        step="RECONCILE_CANDIDATES",
                        prompt="",
                        response="",
                        parsed=json.dumps(_cand_parsed, ensure_ascii=False),
                    )
            except Exception as e:
                logger.debug(f"[reconciler] store RECONCILE_CANDIDATES log failed: {e}")

            # ── Step 2.6: 候选去重（pre-search）──
            # 候选都是链头（is_latest）。只对 L2_FACT + L4_IDENTITY（重复重灾区）
            # 判重，其他层不参与（与 search 链路一致）。命中即置 SHADOW（逻辑删除，
            # 被判重的链头连带其历史链），并从 candidate_map 移除，避免 LLM 看到
            # 重复候选。
            #
            # 分批维度由 owner 决定（group_items_by_owner）：owner=user 的记忆属于
            # 用户这一侧，跨 agent 合并判重；owner=agent 的按 agent 隔离。
            try:
                import time as _time
                from ..pipelines._retrieval.dedup import (
                    DedupItem, execute_dedup, group_items_by_owner,
                )

                _DEDUP_LAYERS = {MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY}
                head_ids = [
                    hid for hid, node in candidate_map.items()
                    if node.layer in _DEDUP_LAYERS
                ]
                _t_emb = _time.perf_counter()
                head_embs = await vector_store.get_embeddings(head_ids)
                _get_emb_ms = round((_time.perf_counter() - _t_emb) * 1000, 2)
                dedup_items = []
                for hid in head_ids:
                    node = candidate_map[hid]
                    emb = head_embs.get(hid)
                    if not emb:
                        continue
                    dedup_items.append(DedupItem(
                        node_id=hid,
                        embedding=emb,
                        content=node.content or "",
                        is_latest=True,
                        is_chain_head=True,
                        gmt_created=(node.gmt_created.timestamp() if node.gmt_created else None),
                        chain_node_ids=chain_members.get(hid, [hid]),
                        # 分批维度：owner 决定跨不跨 agent
                        owner=getattr(node, "owner", None),
                        user_id=getattr(node, "user_id", "") or user_id,
                        agent_id=getattr(node, "agent_id", "") or agent_id,
                        source=getattr(node, "source", "default") or "default",
                    ))

                if len(dedup_items) >= 2:
                    # 每批传各自准确的 user_id/agent_id，DEDUP log 归属才正确
                    for _batch in group_items_by_owner(
                        dedup_items,
                        default_user_id=user_id,
                        default_agent_id=agent_id,
                    ):
                        plan = await execute_dedup(
                            _batch["items"],
                            vector_store=vector_store,
                            cache=cache,
                            trigger="reconcile",
                            request_id=request_id,
                            user_id=_batch["user_id"],
                            agent_id=_batch["agent_id"],
                            delete_from_store=True,
                            extra_parsed={
                                "embedding_source": "vdb_get_embeddings",
                                "get_embeddings_ms": _get_emb_ms,
                                "embed_count": len(head_ids),
                                "embed_ok_count": len(dedup_items),
                                "owner_bucket": _batch["owner"],
                                "source_bucket": _batch["source"],
                                "batch_size": len(_batch["items"]),
                            },
                        )
                        for did in plan.get("delete_ids", []):
                            candidate_map.pop(did, None)
            except Exception as e:
                logger.warning(f"[reconciler] candidate dedup failed: {e}")

            # ── Step 3: 如果没有任何候选，全部 ADD（supersedes=[]）──
            if not candidate_map:
                logger.debug(
                    f"[reconciler] no candidates found, all ADD ({len(new_memories)} items)"
                )
                all_add_ops = []
                if new_memories_with_meta:
                    for meta in new_memories_with_meta:
                        all_add_ops.append(
                            ReconcileOp(
                                op="ADD",
                                content=meta.get("content", ""),
                                layer=meta.get("layer"),
                                owner=meta.get("owner") or "user",
                                tags=list(meta.get("tags") or []),
                                speculate=meta.get("speculate"),
                                topic_paths=meta.get("topic_paths") or [],
                                event_facet=meta.get("event_facet") or {},
                                topic_assignments=meta.get("topic_assignments") or [],
                                topic_paths_provided=bool(
                                    meta.get("topic_paths_provided", "topic_paths" in meta)
                                ),
                                event_facet_provided=bool(
                                    meta.get("event_facet_provided", "event_facet" in meta)
                                ),
                                topic_assignments_provided=bool(meta.get(
                                    "topic_assignments_provided",
                                    "topic_assignments" in meta,
                                )),
                            )
                        )
                else:
                    all_add_ops = [
                        ReconcileOp(op="ADD", content=m) for m in new_memories
                    ]

                if cache and request_id:
                    try:
                        await cache.store_pipeline_log(
                            request_id=request_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            step="RECONCILE",
                            prompt="[skipped - no candidates found in search]",
                            response=json.dumps(
                                [{"op": "ADD", "content": m} for m in new_memories],
                                ensure_ascii=False,
                            ),
                            parsed=json.dumps(
                                [
                                    {"op": o.op, "content": o.content}
                                    for o in all_add_ops
                                ],
                                ensure_ascii=False,
                            ),
                            elapsed_ms=0,
                        )
                    except Exception as e:
                        logger.warning(
                            f"[reconciler] store skipped RECONCILE log failed: {e}"
                        )
                logger.info(
                    f"[reconciler] no candidates → all ADD: "
                    f"{len(new_memories)} new memories → {len(all_add_ops)} ops"
                )
                _log_ops_detail(all_add_ops)
                return ReconcileResult(
                    ops=all_add_ops,
                    success=True,
                    prompt_tokens=_tok_pt,
                    completion_tokens=_tok_ct,
                    total_tokens=_tok_total,
                    candidate_scores=per_memory_candidates,
                    new_memory_embeddings=new_memory_embeddings,
                )

            # ── Step 4: 构造 prompt ──
            _structure_metadata_on = bool(getattr(
                self.config.llm, "structure_metadata_enabled", False
            ))

            # 收集该 user 下所有已有 tags（去重，供 LLM 复用，减少发散）
            _existing_tags_set: set = set()
            try:
                for node in candidate_map.values():
                    if getattr(node, "tags", None):
                        _existing_tags_set.update(node.tags)
            except Exception:
                pass
            # Helper: format memory_at as date string to minute precision
            def _format_memory_at(val) -> Optional[str]:
                if val is None:
                    return None
                if isinstance(val, datetime):
                    return val.strftime("%Y-%m-%d %H:%M")
                if isinstance(val, str) and val:
                    try:
                        dt = datetime.fromisoformat(val)
                        return dt.strftime("%Y-%m-%d %H:%M")
                    except (ValueError, TypeError):
                        return val
                return None

            # new_memories 展示（JSON list 格式，与 existing_memories 对齐）
            if new_memories_with_meta:
                new_mem_list = []
                for meta in new_memories_with_meta:
                    item: Dict[str, Any] = {
                        "content": meta.get("content", ""),
                        "owner": meta.get("owner") or "user",
                        "memory_at": _format_memory_at(current_time) if current_time else None,
                        "tags": list(meta.get("tags") or []),
                    }
                    if _structure_metadata_on and meta.get("topic_paths"):
                        item["topic_paths"] = meta["topic_paths"]
                    if _structure_metadata_on and meta.get("event_facet"):
                        item["event_facet"] = meta["event_facet"]
                    if _structure_metadata_on and meta.get("topic_assignments"):
                        item["topic_assignments"] = meta["topic_assignments"]
                    new_mem_list.append(item)
                new_mem_lines = json.dumps(new_mem_list, ensure_ascii=False, indent=2)
            else:
                new_mem_list = [{"content": m, "owner": "user", "memory_at": _format_memory_at(current_time) if current_time else None, "tags": []} for m in new_memories]
                new_mem_lines = json.dumps(new_mem_list, ensure_ascii=False, indent=2)

            def _sort_key(n):
                t = n.memory_at or n.gmt_created
                return t.isoformat() if t else ""

            sorted_candidates = sorted(candidate_map.values(), key=_sort_key)

            existing_mem_list = []
            for node in sorted_candidates:
                item: Dict[str, Any] = {
                    "memory_id": node.node_id,
                    "content": node.content,
                    "owner": getattr(node, "owner", None) or "user",
                    "memory_at": _format_memory_at(node.memory_at),
                }
                if getattr(node, "tags", None):
                    item["tags"] = list(node.tags)
                if _structure_metadata_on:
                    _custom = getattr(node, "custom", None) or {}
                    if _custom.get("topic_paths"):
                        item["topic_paths"] = _custom["topic_paths"]
                    if _custom.get("event_facet"):
                        item["event_facet"] = _custom["event_facet"]
                    if _custom.get("topic_assignments"):
                        item["topic_assignments"] = _custom["topic_assignments"]
                # 每条演化链只暴露链头（head）的 memory_id + content，**不提供
                # history_versions**：reconcile 只应针对当前有效版本（head）做
                # ADD/UPDATE/SUPERSEDE，暴露历史旧节点会诱导 LLM 误操作（如去
                # supersede 一个已经被取代的旧节点）。链的完整展开只发生在 reader 侧。
                existing_mem_list.append(item)
            existing_lines = json.dumps(existing_mem_list, ensure_ascii=False, indent=2)

            # 构建 current_date（精确到分钟）
            _current_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            prompt = build_reconcile_prompt(
                new_memories=new_memories,
                new_memories_block=new_mem_lines,
                existing_memories_block=existing_lines,
                current_date=_current_date_str,
                few_shot_enabled=bool(
                    getattr(self.config.llm, "few_shot_enabled", False)
                ),
                structure_metadata_enabled=_structure_metadata_on,
            )

            # ── Step 5: LLM 调用 + 重试 ──
            if _log_llm_content_enabled():
                logger.debug(f"[reconciler] full prompt: {prompt!r}")
            else:
                logger.debug(
                    "[reconciler] prompt hidden; len=%d sha256=%s",
                    len(prompt), _content_fingerprint(prompt),
                )
            # 动态估算 max_tokens
            # 取基础值与动态值的较大者，并夹在上限以内。
            dyn_max_tokens = min(
                self.MAX_TOKENS_UPPER_BOUND,
                max(
                    self.MAX_TOKENS,
                    len(new_memories) * self.PER_OP_TOKENS + self.BASE_OVERHEAD_TOKENS,
                ),
            )
            t_recon = _time.perf_counter()
            llm = self._get_llm()
            response = None
            raw_response = ""
            ops = []
            _parse_ok = False
            # JSON 解析失败只重试一次（首次 + 1 次重试 = 共 2 次）。
            # 配合 _parse_ops 的 json_repair 兜底，多数残缺输出首次即可修复。
            # 两次用同一温度（此前是 [base, base] 的列表，等价于常量），
            # 且不再显式传 temperature —— 交给 provider 统一按配置解析，
            # 否则 omit_temperature 会被这里的显式传参绕过。
            _MAX_PARSE_ATTEMPTS = 2
            _llm_error: Optional[str] = None

            for _attempt in range(_MAX_PARSE_ATTEMPTS):
                try:
                    response = await llm.complete(
                        prompt=prompt,
                        max_tokens=dyn_max_tokens,
                        audit_stage="reconcile",
                    )
                except Exception as e:
                    # LLM 彻底不可用（provider 已按 max_retries 重试过，网关 500 /
                    # 连接错误等）。**不能让它冒泡** —— 冒泡会让整批抽取结果连带丢弃。
                    # 交给下面的 ADD-all 兜底。
                    _llm_error = f"{type(e).__name__}: {e}"
                    logger.warning(f"[reconciler] RECONCILE LLM unavailable: {_llm_error}")
                    break
                # 每次 reconcile LLM 调用（含重试）都计入 token 消耗
                _tok_pt += response.prompt_tokens
                _tok_ct += response.completion_tokens
                _tok_total += response.tokens_used
                raw_response = response.content
                ops = self._parse_ops(raw_response)
                ops = self._inherit_exact_input_structure(
                    ops, new_memories_with_meta,
                )
                if _structure_metadata_on:
                    ops = self._resolve_structure_references(ops, candidate_map)

                _is_empty_array = self._strip_code_fence(raw_response) in ("[]", "")
                if ops or _is_empty_array:
                    _parse_ok = True
                    break
                logger.warning(
                    f"[reconciler] RECONCILE JSON parse failed "
                    f"(attempt {_attempt+1}/{_MAX_PARSE_ATTEMPTS}), "
                    f"retrying: raw="
                    f"{raw_response[:200] if _log_llm_content_enabled() else '<redacted>'}"
                )

            recon_elapsed = (_time.perf_counter() - t_recon) * 1000

            # ── Step 6: 解析仍失败 ──
            if not _parse_ok:
                _n_attempts = _MAX_PARSE_ATTEMPTS
                if _llm_error:
                    error_msg = f"RECONCILE LLM unavailable: {_llm_error}"
                    error_code = "LLM_UNAVAILABLE"
                elif raw_response.strip():
                    error_msg = (
                        f"RECONCILE JSON parse failed after {_n_attempts} attempts: "
                        f"{raw_response[:200] if _log_llm_content_enabled() else '<redacted>'}"
                    )
                    error_code = "JSON_PARSE_FAILED"
                else:
                    error_msg = (
                        f"RECONCILE LLM returned empty response after {_n_attempts} attempts"
                    )
                    error_code = "EMPTY_RESPONSE" 
                logger.warning(f"[reconciler] {error_msg}")

                if cache and request_id:
                    try:
                        await cache.store_pipeline_log(
                            request_id=request_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            step="RECONCILE",
                            prompt=prompt,
                            response=raw_response,
                            parsed=json.dumps(
                                {
                                    "error": error_code,
                                    "message": error_msg,
                                    "fallback": "ADD_ALL",
                                    "fallback_count": len(new_memories or []),
                                },
                                ensure_ascii=False,
                            ),
                            elapsed_ms=recon_elapsed,
                            # LLM 彻底不可用时 response 是 None —— 这行本身会抛
                            # AttributeError，而它正好在兜底路径上，会把兜底也一起废掉
                            prompt_tokens=getattr(response, "prompt_tokens", 0) or 0,
                            completion_tokens=getattr(response, "completion_tokens", 0) or 0,
                            total_tokens=getattr(response, "tokens_used", 0) or 0,
                        )
                    except Exception as e:
                        logger.warning(
                            f"[reconciler] store failed RECONCILE log failed: {e}"
                        )

                # 兜底：JSON 解析不出来不等于抽取结果没价值 —— 全部 ADD，别丢内容。
                _fb = self._fallback_add_ops(new_memories, new_memories_with_meta)
                logger.warning(
                    f"[reconciler] falling back to ADD-all: {len(_fb)} memories "
                    f"({error_code})"
                )
                return ReconcileResult(
                    ops=_fb,
                    success=bool(_fb),
                    error=None if _fb else error_msg,
                    degraded=bool(_fb),
                    degraded_reason=error_code,
                    prompt_tokens=_tok_pt,
                    completion_tokens=_tok_ct,
                    total_tokens=_tok_total,
                    new_memory_embeddings=new_memory_embeddings,
                )

            # 写 RECONCILE pipeline log
            if cache and request_id:
                related_ids = []
                for op in ops:
                    if op.op in ("SUPERSEDE", "UPDATE") and op.memory_id:
                        related_ids.append(op.memory_id)
                try:
                    await cache.store_pipeline_log(
                        request_id=request_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        step="RECONCILE",
                        prompt=prompt,
                        response=response.content,
                        parsed=json.dumps(
                            [
                                {
                                    "op": o.op,
                                    "content": o.content,
                                    "layer": o.layer,
                                    "memory_id": o.memory_id,
                                    "tags": o.tags,
                                    "topic_paths": o.topic_paths,
                                    "event_facet": o.event_facet,
                                    "topic_assignments": o.topic_assignments,
                                    "structure_references": o.structure_references,
                                    "related": o.related,
                                    "speculate": o.speculate,
                                    "supersede_reason": o.supersede_reason,
                                    "reason": o.reason,
                                }
                                for o in ops
                            ],
                            ensure_ascii=False,
                        ),
                        memory_ids=related_ids,
                        elapsed_ms=recon_elapsed,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        total_tokens=response.tokens_used,
                    )
                except Exception as e:
                    logger.warning(f"[reconciler] store RECONCILE log failed: {e}")

            logger.info(
                f"[reconciler] reconciled {len(new_memories)} new memories, "
                f"{len(candidate_map)} candidates → {len(ops)} ops"
            )
            _log_ops_detail(ops)
            return ReconcileResult(
                ops=ops,
                success=True,
                prompt_tokens=_tok_pt,
                completion_tokens=_tok_ct,
                total_tokens=_tok_total,
                candidate_scores=per_memory_candidates,
                new_memory_embeddings=new_memory_embeddings,
            )

        except Exception as e:
            logger.error(f"[reconciler] failed: {e}", exc_info=True)
            # 兜底同样适用于「reconcile 链路任意环节炸了」（候选检索、embed、
            # prompt 组装…）。此前这里返回 ops=[] → writer 直接丢弃整批抽取结果。
            _fb = self._fallback_add_ops(new_memories, new_memories_with_meta)
            if _fb:
                logger.warning(
                    f"[reconciler] falling back to ADD-all after unexpected error: "
                    f"{len(_fb)} memories"
                )
                return ReconcileResult(
                    ops=_fb, success=True,
                    degraded=True, degraded_reason=f"EXCEPTION:{type(e).__name__}",
                    new_memory_embeddings=dict(new_memory_embeddings or {}),
                )
            return ReconcileResult(ops=[], success=False, error=str(e))

    @staticmethod
    def _inherit_exact_input_structure(
        ops: List[ReconcileOp],
        new_memories_with_meta: Optional[List[Dict]],
    ) -> List[ReconcileOp]:
        """Preserve extractor structure only when provenance is unambiguous.

        Reconcile may rewrite content, so this intentionally uses exact content
        identity. No fuzzy or positional matching is allowed: uncertain
        provenance remains empty rather than attaching an L2 to the wrong node.
        """
        by_content = {
            str(meta.get("content") or ""): meta
            for meta in (new_memories_with_meta or [])
            if isinstance(meta, dict) and meta.get("content")
        }
        from ..structure import (
            normalize_event_facet,
            normalize_topic_assignments,
            normalize_topic_paths,
        )

        for op in ops:
            meta = by_content.get(str(op.content or ""))
            if not meta:
                continue
            if not op.topic_paths_provided:
                op.topic_paths = normalize_topic_paths(meta.get("topic_paths"))
                op.topic_paths_provided = bool(
                    meta.get("topic_paths_provided", "topic_paths" in meta)
                )
            if not op.event_facet_provided:
                op.event_facet = normalize_event_facet(meta.get("event_facet"))
                op.event_facet_provided = bool(
                    meta.get("event_facet_provided", "event_facet" in meta)
                )
            if not op.topic_assignments_provided:
                op.topic_assignments = normalize_topic_assignments(
                    meta.get("topic_assignments")
                )
                op.topic_assignments_provided = bool(meta.get(
                    "topic_assignments_provided", "topic_assignments" in meta,
                ))
        return ops

    @staticmethod
    def _parse_related(raw: Any) -> List[Dict[str, str]]:
        """解析 op 上的 `related` 字段（关系图用，见 docs/pro_graph_design.md）。

        接受 [{"memory_id": ..., "relation": ...}]，宽容地兼容 target_id / relation_type
        两种键名。只接受白名单里的 relation_type —— 弱模型会自创关系词，放进去只会污染
        边类型先验表。非法项静默丢弃（关系是附加信号，不该让它拖垮主流程）。
        """
        from ..pipelines._graph.projector import LLM_RELATION_TYPES

        if not isinstance(raw, list):
            return []
        out: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = str(
                item.get("memory_id") or item.get("target_id") or ""
            ).strip()
            rtype = str(
                item.get("relation") or item.get("relation_type") or ""
            ).strip().lower()
            if not target or rtype not in LLM_RELATION_TYPES:
                continue
            out.append({"target_id": target, "relation_type": rtype})
        return out

    @staticmethod
    def _resolve_structure_references(
        ops: List[ReconcileOp], candidate_map: Dict[str, Any],
    ) -> List[ReconcileOp]:
        """Resolve only explicit, in-candidate path references."""
        from ..structure import canonicalize_assignments_from_references

        existing_custom = {
            str(memory_id): dict(getattr(node, "custom", None) or {})
            for memory_id, node in (candidate_map or {}).items()
        }
        for op in ops:
            if not op.structure_references:
                continue
            resolved = canonicalize_assignments_from_references(
                op.topic_assignments,
                op.event_facet,
                op.structure_references,
                existing_custom,
            )
            if resolved:
                op.topic_assignments = resolved
                op.topic_assignments_provided = True
        return ops

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """剥掉 ```json ... ``` / ``` ... ``` markdown 围栏，返回内层内容。"""
        text = (text or "").strip()
        if "```json" in text:
            return text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            return text.split("```")[1].split("```")[0].strip()
        return text

    def _parse_ops(self, text: str) -> List[ReconcileOp]:
        """
        解析 LLM 返回的 RECONCILE 输出。

        支持格式：
        - Flat list（推荐）：[{op, content, ...}, {op, content, ...}]
        - Group 格式（向后兼容）：[{"reason": ..., "ops": [...]}]

        支持的 op 类型：ADD, SUPERSEDE, UPDATE
        """
        text = self._strip_code_fence(text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            data = None
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    data = None
            if data is None:
                # 兜底：json_repair 修复常见 LLM 残缺输出（缺逗号/引号/未闭合等）
                try:
                    from json_repair import repair_json

                    repaired = repair_json(text, return_objects=True)
                    if repaired not in (None, "", {}):
                        logger.info("[reconciler] JSON recovered via json_repair")
                        data = repaired
                except Exception as _re:
                    logger.debug(f"[reconciler] json_repair failed: {_re}")
            if data is None:
                logger.warning(f"[reconciler] failed to parse ops JSON: {text[:200]}")
                return []

        if not isinstance(data, list):
            data = [data]

        # ── 展平：把 (op_dict, group_reason) 二元组放到 flat 列表里 ──
        flat_items: List[tuple] = []  # (op_dict, group_reason)
        for entry in data:
            if not isinstance(entry, dict):
                continue
            # 分支 A：group 结构（向后兼容）— 带 reason + ops 子列表
            if "ops" in entry and isinstance(entry.get("ops"), list):
                group_reason = str(entry.get("reason") or "").strip()
                for op_dict in entry["ops"]:
                    if isinstance(op_dict, dict):
                        flat_items.append((op_dict, group_reason))
            # 分支 B：扁平 op 结构（推荐）
            elif "op" in entry:
                flat_items.append((entry, ""))
            else:
                logger.warning(
                    f"[reconciler] unrecognized entry shape, skipped: {entry}"
                )

        # Pass 1: 结构解析
        parsed: List[ReconcileOp] = []
        for op_dict, group_reason in flat_items:
            op_type = str(op_dict.get("op", "")).upper()
            op_reason = str(op_dict.get("reason") or "").strip() or group_reason

            # 通用字段提取
            raw_tags = op_dict.get("tags", []) or []
            if not isinstance(raw_tags, list):
                raw_tags = [raw_tags]
            # 保留专名大小写（Jasper / Canada）；只做 strip，不再 lower。
            # 主题词 LLM 仍可给小写；NER 属性写入 tag_ners。
            tags = [str(t).strip() for t in raw_tags if t and str(t).strip()][:3]
            try:
                from ..pipelines._retrieval.ner import annotate_tag_ners
                tag_ners = annotate_tag_ners(tags)
            except Exception:
                tag_ners = [""] * len(tags)
            speculate = op_dict.get("speculate") or None
            if speculate and isinstance(speculate, str):
                speculate = speculate.strip() or None

            # owner：仅接受 user/agent（assistant→agent），否则 None
            _raw_owner = op_dict.get("owner")
            owner = None
            if _raw_owner:
                _ov = str(_raw_owner).strip().lower()
                if _ov in ("user", "agent"):
                    owner = _ov
                elif _ov == "assistant":
                    owner = "agent"

            related = self._parse_related(op_dict.get("related"))
            from ..structure import (
                normalize_event_facet,
                normalize_structure_references,
                normalize_topic_assignments,
                normalize_topic_paths,
            )
            topic_paths = normalize_topic_paths(op_dict.get("topic_paths"))
            event_facet = normalize_event_facet(op_dict.get("event_facet"))
            topic_assignments = normalize_topic_assignments(
                op_dict.get("topic_assignments")
            )
            structure_references = normalize_structure_references(
                op_dict.get("structure_references")
            )
            topic_paths_provided = "topic_paths" in op_dict
            event_facet_provided = "event_facet" in op_dict
            topic_assignments_provided = "topic_assignments" in op_dict

            if op_type == "ADD":
                parsed.append(
                    ReconcileOp(
                        op="ADD",
                        content=op_dict.get("content"),
                        layer=op_dict.get("layer"),
                        owner=owner,
                        tags=tags,
                        tag_ners=tag_ners,
                        topic_paths=topic_paths,
                        event_facet=event_facet,
                        topic_assignments=topic_assignments,
                        structure_references=structure_references,
                        topic_paths_provided=topic_paths_provided,
                        event_facet_provided=event_facet_provided,
                        topic_assignments_provided=topic_assignments_provided,
                        speculate=speculate,
                        related=related,
                        reason=op_reason,
                    )
                )

            elif op_type == "SUPERSEDE":
                # 接受单 id（memory_id）或多 id 链（memory_ids，有序 旧→新）
                raw_ids = op_dict.get("memory_ids")
                id_list: List[str] = []
                if isinstance(raw_ids, list):
                    id_list = [str(x).strip() for x in raw_ids if x and str(x).strip()]
                mid = str(op_dict.get("memory_id") or "").strip()
                if mid and mid not in id_list:
                    # memory_id 视为链上最新的目标，追加到末尾
                    id_list.append(mid)
                if not id_list:
                    logger.warning(
                        f"[reconciler] SUPERSEDE op missing memory_id(s), skipped: {op_dict}"
                    )
                    continue
                # memory_id 始终指向链上最新的现有节点（新 head 直接 supersede 它）
                parsed.append(
                    ReconcileOp(
                        op="SUPERSEDE",
                        memory_id=id_list[-1],
                        memory_ids=id_list,
                        content=op_dict.get("content"),
                        layer=op_dict.get("layer"),
                        owner=owner,
                        tags=tags,
                        tag_ners=tag_ners,
                        topic_paths=topic_paths,
                        event_facet=event_facet,
                        topic_assignments=topic_assignments,
                        structure_references=structure_references,
                        topic_paths_provided=topic_paths_provided,
                        event_facet_provided=event_facet_provided,
                        topic_assignments_provided=topic_assignments_provided,
                        speculate=speculate,
                        supersede_reason=str(
                            op_dict.get("supersede_reason") or ""
                        ).strip(),
                        related=related,
                        reason=op_reason,
                    )
                )

            elif op_type == "UPDATE":
                mid = str(op_dict.get("memory_id") or "").strip()
                if not mid:
                    logger.warning(
                        f"[reconciler] UPDATE op missing memory_id, skipped: {op_dict}"
                    )
                    continue
                parsed.append(
                    ReconcileOp(
                        op="UPDATE",
                        memory_id=mid,
                        content=op_dict.get("content"),
                        layer=op_dict.get("layer"),
                        owner=owner,
                        tags=tags,
                        tag_ners=tag_ners,
                        topic_paths=topic_paths,
                        event_facet=event_facet,
                        topic_assignments=topic_assignments,
                        structure_references=structure_references,
                        topic_paths_provided=topic_paths_provided,
                        event_facet_provided=event_facet_provided,
                        topic_assignments_provided=topic_assignments_provided,
                        speculate=speculate,
                        related=related,
                        reason=op_reason,
                    )
                )

            elif op_type == "DELETE":
                # 向后兼容：旧 prompt 可能仍返回 DELETE，映射为 SHADOW 旧节点
                mid = str(op_dict.get("memory_id") or "").strip()
                if not mid:
                    logger.warning(
                        f"[reconciler] DELETE op missing memory_id, skipped: {op_dict}"
                    )
                    continue
                logger.info(
                    f"[reconciler] legacy DELETE op mapped (memory_id={mid}), "
                    f"will shadow the node"
                )
                parsed.append(
                    ReconcileOp(
                        op="UPDATE",
                        memory_id=mid,
                        content=None,  # 标记为无 content 的 shadow-only op
                        reason=op_reason,
                    )
                )

            else:
                logger.warning(
                    f"[reconciler] unknown op '{op_type}', skipped: {op_dict}"
                )
                continue

        # Pass 2: 校验"同一 existing memory 不得被多处 touch"
        touched: Dict[str, str] = {}  # memory_id → first-seen op description
        validated: List[ReconcileOp] = []
        for op in parsed:
            # SUPERSEDE 多节点链：登记链上所有 id；UPDATE/单节点 SUPERSEDE 登记 memory_id
            if op.op == "SUPERSEDE" and op.memory_ids:
                target_ids = list(op.memory_ids)
            elif op.op in ("SUPERSEDE", "UPDATE"):
                target_ids = [op.memory_id] if op.memory_id else []
            else:
                target_ids = []
            if target_ids:
                clash = next((t for t in target_ids if t in touched), None)
                if clash is not None:
                    logger.warning(
                        f"[reconciler] memory_id {clash} already touched by '{touched[clash]}', "
                        f"current op '{op.op} ({op.reason[:60]})' dropped"
                    )
                    continue
                for t in target_ids:
                    touched[t] = f"{op.op}({op.reason[:40]})"
            validated.append(op)

        return validated
