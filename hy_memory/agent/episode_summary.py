"""Episode-scoped summary：一次讨论结束时总结一次，而不是每攒够 N 轮就重写一遍。

与固定窗口摘要的区别
--------------------------------------------------

固定窗口摘要按 user turn 数积累：每达到一个窗口就触发一次，
并且要把**上一条摘要文本喂回 LLM** 做延续/合并，旧摘要置 SHADOW。
对一次长讨论（十几二十轮、跨几次 write）来说这意味着：

    turn 10 → 调 LLM（输入 10 轮）
    turn 20 → 调 LLM（输入 prev_summary + 10 轮），把 turn 10 的产出作废
    turn 30 → 调 LLM（输入 prev_summary + 10 轮），把 turn 20 的产出作废

付了 3 次 token、上下文还越来越长，最终只留下 1 条摘要。

Episode 划分是**免费的**（按写入时间间隔切，不用 LLM），而且天然对应"一件事"。
于是可以换成：**等这件事结束了，总结一次**。

    episode 关闭 → 调 LLM（输入该 episode 的全部记忆）→ 1 条摘要，无需 merge

一次讨论只付一次 token，也不产生"写了又作废"的中间摘要。

摘要从**已抽取的记忆**出发（而不是原始对话轮），这和 topic summary 的做法一致：
记忆本身就是我们要留下的东西，且比原始轮次省 token。代价是没进记忆的闲聊细节不会进摘要 ——
对检索用途来说这是想要的行为。

状态不额外建表，存在摘要节点自己的 `custom` 里：

    custom = {
        "summary_kind": "episode",
        "episode_id": ...,
        "covered_count": N,
        "source_memory_ids": [...],
    }
"""

import asyncio
import logging
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from ..utils.source_filter import merge_sources

logger = logging.getLogger(__name__)

SUMMARY_KIND_EPISODE = "episode"
SUMMARY_KIND_SESSION_EPISODE = "session_episode"
SUMMARY_KIND_DYNAMIC_EPISODE = "dynamic_episode"
SUMMARY_KIND_SERIES_EPISODE = "series_episode"


DYNAMIC_BOUNDARY_PROMPT = """You are an episodic-memory boundary detector. Split a complete conversation
into durable, independently retrievable events.

Default to merging messages that develop the same event. Split when a completed
real-world event, activity, trip, problem, achievement, plan, or purpose is followed
by another event that could be asked about separately later. Smooth social
transitions do not make different events one memory. For example, recipe/health,
painting classes, and a later skiing trip should be separate episodes.

Keep greetings, reactions, encouragement, and farewells with the event they support.
Avoid fragments containing only acknowledgements.

Return JSON only:
{{"reasoning":"brief explanation","event_count":3,"boundaries":[10,22]}}

Consistency requirement: event_count MUST equal len(boundaries)+1. If your reasoning
describes several separate events, boundaries cannot be empty. A boundary is the
1-based message number AFTER which an episode ends. Never include the final message.

Session time: {timestamp}
Messages:
{messages}
"""


DYNAMIC_BOUNDARY_CORRECTION_PROMPT = """Your previous boundary JSON was internally inconsistent.

Previous output:
{previous}

Return a corrected JSON object only. `event_count` must equal len(boundaries)+1, and
the boundary indices must correspond to the distinct events named in the reasoning.
Valid boundary range is 1 through {last_boundary}.
"""


DYNAMIC_CAPSULE_PROMPT = """Convert this ONE coherent conversation episode into a compact retrieval capsule.

The capsule will be combined with exact atomic memories already matched by search.
It should restore context and completeness without repeating every conversational turn.

Rules:
- Write a third-person narrative of roughly 100-180 English tokens.
- Preserve full names and attribution: who did or felt what.
- Preserve dates, places, numbers, named items, causes, outcomes, and whether an
  event was planned or completed.
- Preserve complete short lists when the conversation provides them.
- Remove greetings, encouragement, repeated agreement, and generic advice unless
  they change the event outcome.
- Do not invent or infer unstated relationships.
- Use the session timestamp to clarify relative time, while keeping the original
  relation when useful.

Return JSON only:
{{"title":"specific retrieval title","summary":"compact narrative"}}

Session time: {timestamp}
Messages:
{messages}
"""


EPISODE_SUMMARY_PROMPT = """You are summarizing ONE episode of conversation with the user.

An episode is a single stretch of discussion — one thing that was talked about,
possibly across several sessions of writing.

## Memories captured during this episode (oldest → newest)

{memories}

---

Write a single dense summary of what happened / what was discussed in this episode.

Rules:

* 2–4 sentences, third person, starting with "The user ..." (or the named person
  if the memories are about someone else).
* Tie the memories together into one coherent account — an episode usually has a
  thread running through it (a plan, a problem, an outing, a decision).
* Preserve concrete details: names, places, numbers, dates.
* Do NOT invent anything that is not in the memories.
* Do NOT enumerate the memories one by one; synthesize them.
* Output language must match the language of the memories.

Return ONLY the summary text, no preamble, no JSON, no quotes."""


EPISODE_SUMMARY_PROMPT_ZH = """你在为**一段讨论**（一个 episode）做总结。

一个 episode 是一段连续的讨论 —— 围绕一件事，可能跨越好几次写入。

## 这段讨论中抽取到的记忆（时间正序）

{memories}

---

写一段关于这次讨论内容的高密度摘要。

要求：

* 2–4 句，第三人称，以"用户…"开头（若记忆讲的是别人，则以那个人的名字开头）。
* 把这些记忆串成一个连贯的叙述 —— 一段讨论通常有一根主线（一个计划、一个问题、
  一次出行、一个决定）。
* 保留具体细节：人名、地点、数字、日期。
* 绝不编造记忆里没有的内容。
* 不要逐条罗列记忆，要综合成一段。
* 输出语言与记忆的语言一致。

只返回摘要正文，不要任何前言、不要 JSON、不要引号。"""


SESSION_EPISODE_PROMPT = """Convert this complete conversation session into one retrieval-friendly episode narrative.

Rules:
- Write a coherent third-person account, not a bullet list and not dialogue.
- Preserve all concrete facts that could answer a later question: full person names, who did what, dates, sequence, numbers, places, reasons, feelings, decisions, and completed events versus plans.
- Resolve relative dates against the supplied session date and include the absolute date in parentheses while preserving the original relation when useful.
- Keep different people clearly attributed. Do not replace names with an ambiguous 'the user'.
- Do not add facts or general advice that was not stated.
- Be concise but complete. Return only the narrative.

Session date: {timestamp}
Conversation:
{conversation}
"""


def session_episode_id(user_id: str, agent_id: str, session_id: str) -> str:
    """Stable UUID makes explicit finalize idempotent across vector backends."""
    raw = "\0".join((user_id or "", agent_id or "default_agent",
                     session_id or "default_session"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hy-memory:session-episode:" + raw))


def _parse_json_object(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        from json_repair import repair_json
        value = json.loads(repair_json(raw))
    if not isinstance(value, dict):
        raise ValueError("episode response must be a JSON object")
    return value


def _raw_messages(raw_nodes: List[Any]) -> List[dict]:
    """Recover ordered role/name messages from stored L1_RAW chunks."""
    messages: List[dict] = []
    pattern = re.compile(r"(?m)^\[([^\]]+)\]:\s*")
    for node in raw_nodes:
        content = (getattr(node, "content", "") or "").strip()
        matches = list(pattern.finditer(content))
        if not matches:
            if content:
                messages.append({
                    "speaker": "unknown", "content": content,
                    "source_raw_memory_id": node.node_id,
                })
            continue
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = content[match.end():end].strip()
            if body:
                messages.append({
                    "speaker": match.group(1).strip(), "content": body,
                    "source_raw_memory_id": node.node_id,
                })
    return messages


def _render_messages(messages: List[dict]) -> str:
    return "\n".join(
        f"[{index}] {message['speaker']}: {message['content']}"
        for index, message in enumerate(messages, 1)
    )


def _dynamic_episode_id(
    user_id: str, agent_id: str, session_id: str, raw_digest: str,
    start: int, end: int,
) -> str:
    raw = "\0".join((user_id, agent_id, session_id, raw_digest, str(start), str(end)))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hy-memory:dynamic-episode:" + raw))


def _series_episode_id(
    user_id: str, agent_id: str, member_ids: List[str],
) -> str:
    """Stable identity for one cross-session episode component."""
    raw = "\0".join((user_id, agent_id, *sorted(set(member_ids))))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hy-memory:series-episode:" + raw))


def _series_memory_at(times: List[int]) -> Optional[datetime]:
    """Earliest member timestamp as the same local wall clock it came from.

    ``_node_timestamp`` produces ``datetime.timestamp()`` values. Reading them
    back with ``utcfromtimestamp`` turns them into a naive *UTC* datetime, which
    the vector payload later re-interprets as local time and shifts by the
    machine's UTC offset.
    """
    return datetime.fromtimestamp(min(times)) if times else None


def _node_timestamp(node: Any) -> Optional[int]:
    value = getattr(node, "memory_at", None) or getattr(node, "gmt_created", None)
    if value is None:
        return None
    try:
        return int(value.timestamp())
    except (AttributeError, TypeError, OSError, ValueError):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _series_episode_label(facts: List[Any], episode_count: int, session_count: int) -> tuple[str, str]:
    """Build small navigational text without copying any member L2 body."""
    tag_sets = []
    original_by_key: Dict[str, str] = {}
    for fact in facts:
        keys = set()
        tags = list(getattr(fact, "tags", None) or [])
        tag_ners = list(getattr(fact, "tag_ners", None) or [])
        for index, tag in enumerate(tags):
            text = str(tag or "").strip()
            ner = str(tag_ners[index] if index < len(tag_ners) else "").upper()
            if not text or ner == "PERSON":
                continue
            key = re.sub(r"\s+", " ", text.casefold())
            if len(key) > 4 and key.endswith("s") and not key.endswith("ss"):
                key = key[:-1]
            keys.add(key)
            original_by_key.setdefault(key, text)
        if keys:
            tag_sets.append(keys)
    shared = set.intersection(*tag_sets) if tag_sets else set()
    generic = {
        "event", "events", "experience", "experiences", "activity", "activities",
        "memory", "memories", "personal", "user",
    }
    labels = [original_by_key[key] for key in sorted(shared) if key not in generic]
    title = ", ".join(labels[:3]) + " timeline" if labels else "Related events across time"
    intro = (
        f"A timeline of {episode_count} explicitly linked episodes "
        f"across {session_count} sessions."
    )
    return title[:500], intro


def _series_episode_plans(nodes: List[Any], edges: List[Dict[str, Any]]) -> List[dict]:
    """Plan cross-session containers from exact provenance or typed relations.

    Tags, generic semantic similarity and co-occurrence are intentionally ignored.
    """
    from ..models.memory import MemoryLayer, MemoryStatus
    from ..pipelines._retrieval.session_episode import parent_episode_ids

    active_l2 = {
        node.node_id: node for node in nodes
        if getattr(node, "layer", None) == MemoryLayer.L2_FACT
        and getattr(node, "status", None) == MemoryStatus.ACTIVE
        and bool(getattr(node, "is_latest", True))
    }
    episodes = {
        node.node_id: node for node in nodes
        if getattr(node, "layer", None) == MemoryLayer.L3_SUMMARY
        and getattr(node, "status", None) == MemoryStatus.ACTIVE
        and bool(getattr(node, "is_latest", True))
        and (getattr(node, "custom", None) or {}).get("summary_kind")
            == SUMMARY_KIND_DYNAMIC_EPISODE
    }
    if len(episodes) < 2 or not active_l2:
        return []

    episode_ids_by_fact: Dict[str, List[str]] = {}
    for fact_id, fact in active_l2.items():
        item = {"node_id": fact_id, "node": fact}
        episode_ids_by_fact[fact_id] = [
            episode_id for episode_id in parent_episode_ids(item)
            if episode_id in episodes
        ]

    # Components are formed over facts, not episodes. A single session may discuss
    # unrelated subjects, so using episode IDs as graph vertices creates bridges.
    parent: Dict[str, str] = {}
    evidence: List[dict] = []

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for fact_id, episode_ids in episode_ids_by_fact.items():
        session_ids = {
            str(getattr(episodes[episode_id], "session_id", "") or "default_session")
            for episode_id in episode_ids
        }
        if len(episode_ids) < 2 or len(session_ids) < 2:
            continue
        find(fact_id)
        evidence.append({
            "type": "shared_l2_provenance",
            "memory_id": fact_id,
            "episode_ids": list(episode_ids),
        })

    for edge in edges or []:
        if str(edge.get("relation_type") or "") != "same_series":
            continue
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        source_episodes = episode_ids_by_fact.get(source_id) or []
        target_episodes = episode_ids_by_fact.get(target_id) or []
        source_sessions = {
            str(getattr(episodes[value], "session_id", "") or "default_session")
            for value in source_episodes
        }
        target_sessions = {
            str(getattr(episodes[value], "session_id", "") or "default_session")
            for value in target_episodes
        }
        if source_episodes and target_episodes and len(source_sessions | target_sessions) >= 2:
            union(source_id, target_id)
            evidence.append({
                "type": "same_series",
                "source_id": source_id,
                "target_id": target_id,
                "episode_ids": list(dict.fromkeys(source_episodes + target_episodes)),
            })

    components: Dict[str, List[str]] = {}
    for fact_id in parent:
        components.setdefault(find(fact_id), []).append(fact_id)

    plans = []
    for component_member_ids in components.values():
        component_member_ids = sorted(set(component_member_ids))
        component_episode_ids = list(dict.fromkeys(
            episode_id
            for fact_id in component_member_ids
            for episode_id in episode_ids_by_fact.get(fact_id) or []
        ))
        component_episodes = [episodes[episode_id] for episode_id in component_episode_ids]
        session_ids = {
            str(getattr(episode, "session_id", "") or "default_session")
            for episode in component_episodes
        }
        if len(component_episode_ids) < 2 or len(session_ids) < 2:
            continue
        component_episodes.sort(key=lambda node: (
            _node_timestamp(node) is None,
            _node_timestamp(node) or 0,
            node.node_id,
        ))
        timeline = []
        all_member_ids: List[str] = []
        for episode in component_episodes:
            member_ids = [
                fact_id for fact_id in component_member_ids
                if episode.node_id in (episode_ids_by_fact.get(fact_id) or [])
            ]
            member_ids = list(dict.fromkeys(member_ids))
            if not member_ids:
                continue
            all_member_ids.extend(member_ids)
            timeline.append({
                "episode_id": episode.node_id,
                "session_id": str(
                    getattr(episode, "session_id", "") or "default_session"
                ),
                "memory_at": _node_timestamp(episode),
                "member_ids": member_ids,
            })
        all_member_ids = list(dict.fromkeys(all_member_ids))
        # A cross-session container must connect distinct user-visible memories.
        # One reconciled L2 can legitimately retain provenance from several raw
        # sessions; repeating that same ID in multiple dated segments does not add
        # evidence and only creates an extra L3 retrieval entry around identical
        # content.  Keep such provenance on the L2 itself, but do not manufacture a
        # series episode unless there are at least two canonical L2 members.
        if len(timeline) < 2 or len(all_member_ids) < 2:
            continue
        title, intro = _series_episode_label(
            [active_l2[fact_id] for fact_id in all_member_ids],
            len(timeline), len(session_ids),
        )
        component_set = set(component_member_ids)
        component_evidence = []
        for item in evidence:
            referenced = {
                str(value) for value in (
                    [item.get("memory_id")]
                    if item.get("memory_id") else
                    [item.get("source_id"), item.get("target_id")]
                ) if value
            }
            if referenced and referenced <= component_set:
                component_evidence.append(item)
        plans.append({
            "episode_ids": component_episode_ids,
            "session_ids": sorted(session_ids),
            "member_memory_ids": all_member_ids,
            "timeline": timeline,
            "title": title,
            "intro": intro,
            "evidence": component_evidence,
        })
    return sorted(plans, key=lambda plan: tuple(plan["episode_ids"]))


def _choose_fact_parent(
    fact: Any,
    fact_vector: List[float],
    episode_nodes: List[Any],
    episode_embeddings: dict[str, List[float]],
) -> tuple[Any, float, str]:
    """Prefer exact raw provenance; use semantic similarity only to disambiguate."""
    source_raw_ids = _source_ids(fact)
    exact = [
        node for node in episode_nodes
        if source_raw_ids & set(
            (getattr(node, "custom", None) or {}).get("source_raw_memory_ids") or []
        )
    ]
    candidates = exact or episode_nodes
    if len(exact) == 1:
        return exact[0], 1.0, "source_raw_unique"
    parent = max(
        candidates,
        key=lambda node: _cosine(
            fact_vector, episode_embeddings.get(node.node_id) or node.embedding or []
        ),
    )
    similarity = _cosine(
        fact_vector, episode_embeddings.get(parent.node_id) or parent.embedding or []
    )
    return parent, similarity, "source_raw_semantic" if exact else "semantic_fallback"


def _normalized_centroid(vectors: List[List[float]]) -> List[float]:
    """Unit-normalized mean vector for an episode's referenced L2 members."""
    import math

    valid = [vector for vector in vectors if vector]
    if not valid:
        return []
    width = len(valid[0])
    valid = [vector for vector in valid if len(vector) == width]
    if not valid:
        return []
    mean = [sum(vector[i] for vector in valid) / len(valid) for i in range(width)]
    norm = math.sqrt(sum(value * value for value in mean))
    return [value / norm for value in mean] if norm else mean


def _source_ids(node: Any) -> set[str]:
    custom = getattr(node, "custom", None) or {}
    values = set(str(value) for value in custom.get("source_raw_memory_ids") or [] if value)
    legacy = str(getattr(node, "source_raw_memory_id", "") or "")
    if legacy:
        values.add(legacy)
    return values


def _append_episode_membership(
    fact: Any, episode_id: str, similarity: float, method: str,
) -> dict:
    """Attach an episode without erasing memberships from other source sessions."""
    from ..pipelines._retrieval.session_episode import add_parent_episode_membership

    return add_parent_episode_membership(
        getattr(fact, "custom", None), episode_id,
        similarity=similarity, method=method,
    )


def _primary_episode_id(fact: Any, fallback: str) -> str:
    """Preserve the first/legacy parent when adding another membership."""
    direct = str(getattr(fact, "episode_id", "") or "")
    if direct:
        return direct
    custom = getattr(fact, "custom", None) or {}
    legacy = str(custom.get("parent_episode_id") or "")
    return legacy or fallback


def _word_overlap(left: str, right: str) -> float:
    def words(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").casefold()))

    a, b = words(left), words(right)
    return len(a & b) / len(a) if a else 0.0


def _assign_facts_to_segments(facts: List[Any], segments: List[dict]) -> Dict[int, List[Any]]:
    """Assign each L2 to at most one generated segment without extra embeddings."""
    by_segment: Dict[int, List[Any]] = {index: [] for index in range(len(segments))}
    for fact in facts:
        provenance = _source_ids(fact)
        exact = [
            index for index, segment in enumerate(segments)
            if provenance & set(segment.get("source_raw_memory_ids") or [])
        ]
        candidates = exact or list(range(len(segments)))
        if not candidates:
            continue
        scored = [
            (
                _word_overlap(
                    getattr(fact, "content", ""),
                    " ".join((segments[index].get("title") or "", segments[index].get("summary") or "")),
                ),
                -index,
                index,
            )
            for index in candidates
        ]
        score, _neg_index, chosen = max(scored)
        if not exact and score <= 0:
            continue
        by_segment[chosen].append(fact)
    return by_segment


async def finalize_dynamic_episode_capsules(
    *, vector_store, embed_service, llm_provider, user_id: str,
    agent_id: Optional[str], session_id: str, summary_concurrency: int = 3,
) -> dict:
    """Split a completed session, create compact L3 capsules, and link its facts.

    Boundary and summary generation are separate calls. Boundary failure degrades
    to one whole-session capsule. A segmented-summary failure also retries the
    session as one capsule so the write never loses the complete event context.
    """
    from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus, SourceType

    narrow_queries = bool(getattr(
        vector_store, "_supports_narrow_episode_queries", False,
    ))
    if narrow_queries:
        session_nodes = await vector_store.list_by_session(
            user_id=user_id, agent_id=agent_id, limit=10000,
            session_id=session_id,
            status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW, MemoryStatus.SUPERSEDED],
            layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY],
            only_latest=False, with_vectors=False, allow_raw_layer=True,
        )
        nodes = list(session_nodes)
    else:
        nodes = await vector_store.list_by_user(
            user_id=user_id, agent_id=agent_id, limit=10000,
            status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW, MemoryStatus.SUPERSEDED],
            layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY],
            only_latest=False, with_vectors=False, allow_raw_layer=True,
        )
        session_nodes = [
            n for n in nodes
            if (n.session_id or "default_session") == session_id
        ]
    raw_nodes = [n for n in session_nodes if n.layer == MemoryLayer.L1_RAW]
    raw_nodes.sort(key=lambda n: (n.memory_at or n.gmt_created, n.gmt_created, n.node_id))
    unique_raw_nodes, seen = [], set()
    for node in raw_nodes:
        normalized = re.sub(r"\s+", " ", (node.content or "").strip())
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_raw_nodes.append(node)
    raw_nodes = unique_raw_nodes
    messages = _raw_messages(raw_nodes)
    if not messages:
        return {"status": "skipped", "reason": "no_raw_messages", "session_id": session_id}

    raw_digest = hashlib.sha256(
        "\0".join(re.sub(r"\s+", " ", n.content.strip()) for n in raw_nodes).encode()
    ).hexdigest()
    current_raw_ids = {n.node_id for n in raw_nodes}
    if narrow_queries:
        provenance_nodes = await vector_store.list_by_source_raw_ids(
            list(current_raw_ids),
            user_id=user_id,
            agent_id=agent_id,
            limit=10000,
            status_filter=[MemoryStatus.ACTIVE],
            layers=[MemoryLayer.L2_FACT],
            only_latest=True,
            with_vectors=False,
        )
        nodes_by_id = {
            node.node_id: node for node in [*session_nodes, *provenance_nodes]
        }
        nodes = list(nodes_by_id.values())
    dynamic_nodes = [n for n in session_nodes
                     if n.layer == MemoryLayer.L3_SUMMARY
                     and (n.custom or {}).get("summary_kind") == SUMMARY_KIND_DYNAMIC_EPISODE]
    existing = [n for n in dynamic_nodes
                if (n.custom or {}).get("source_raw_sha256") == raw_digest]
    existing_spans = sorted(
        (int((n.custom or {}).get("message_start") or 0),
         int((n.custom or {}).get("message_end") or 0))
        for n in existing
    )
    expected_start = 1
    complete_existing = bool(existing_spans)
    for start, end in existing_spans:
        if start != expected_start or end < start:
            complete_existing = False
            break
        expected_start = end + 1
    complete_existing = complete_existing and expected_start == len(messages) + 1
    if complete_existing:
        existing_embeddings = await vector_store.get_embeddings(
            [n.node_id for n in existing]
        )
        fact_nodes = [
            n for n in nodes
            if n.layer == MemoryLayer.L2_FACT
            and n.status == MemoryStatus.ACTIVE
            and getattr(n, "is_latest", True)
            and (
                n in session_nodes
                or bool(_source_ids(n) & current_raw_ids)
            )
        ]
        fact_embeddings = await vector_store.get_embeddings([n.node_id for n in fact_nodes])
        linked = 0
        members_by_episode: dict[str, list[str]] = {
            node.node_id: [] for node in existing
        }
        for fact in fact_nodes:
            vector = fact_embeddings.get(fact.node_id)
            if not vector:
                continue
            parent, similarity, method = _choose_fact_parent(
                fact, vector, existing, existing_embeddings,
            )
            custom = _append_episode_membership(
                fact, parent.node_id, similarity, method,
            )
            if await vector_store.update_payload(
                fact.node_id,
                {
                    "episode_id": _primary_episode_id(fact, parent.node_id),
                    "custom": custom,
                },
            ):
                linked += 1
                members_by_episode.setdefault(parent.node_id, []).append(fact.node_id)
        for episode in existing:
            member_ids = sorted(set(members_by_episode.get(episode.node_id) or []))
            custom = dict(episode.custom or {})
            custom["member_memory_ids"] = member_ids
            custom["timeline"] = [{
                "memory_at": (
                    int(episode.memory_at.timestamp())
                    if getattr(episode, "memory_at", None) else None
                ),
                "member_ids": member_ids,
            }]
            await vector_store.update_payload(episode.node_id, {"custom": custom})
            episode.custom = custom
        return {
            "status": "reused", "session_id": session_id,
            "episode_ids": [n.node_id for n in existing],
            "episode_count": len(existing), "raw_count": len(raw_nodes),
            "linked_fact_count": linked,
            "prompt_tokens": 0, "completion_tokens": 0,
        }

    # A previous interrupted finalize can leave only some capsules. They are safe
    # to shadow because they are derived L3 nodes for this same session; regenerate
    # the complete set below before relinking facts.
    for node in dynamic_nodes:
        await vector_store.update_payload(node.node_id, {
            "status": MemoryStatus.SHADOW.value,
            "is_latest": False,
        })

    timestamp = min((n.memory_at for n in raw_nodes if n.memory_at), default=None)
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "unknown"
    rendered = _render_messages(messages)
    prompt_tokens = completion_tokens = 0
    boundary_fallback = False
    try:
        boundary_response = await llm_provider.complete(
            prompt=DYNAMIC_BOUNDARY_PROMPT.format(
                timestamp=timestamp_text, messages=rendered,
            ),
            max_tokens=4096,
        )
        prompt_tokens += getattr(boundary_response, "prompt_tokens", 0) or 0
        completion_tokens += getattr(boundary_response, "completion_tokens", 0) or 0
        boundary_blob = _parse_json_object(boundary_response.content)
        def _validated_boundaries(blob: dict) -> List[int]:
            cuts = sorted({
                int(value) for value in (blob.get("boundaries") or [])
                if 1 <= int(value) < len(messages)
            })
            event_count = int(blob.get("event_count") or 0)
            if event_count != len(cuts) + 1:
                raise ValueError(
                    f"event_count={event_count} does not match {len(cuts)} boundaries"
                )
            return cuts

        try:
            boundaries = _validated_boundaries(boundary_blob)
        except ValueError:
            correction = await llm_provider.complete(
                prompt=DYNAMIC_BOUNDARY_CORRECTION_PROMPT.format(
                    previous=json.dumps(boundary_blob, ensure_ascii=False),
                    last_boundary=len(messages) - 1,
                ),
                max_tokens=2048,
            )
            prompt_tokens += getattr(correction, "prompt_tokens", 0) or 0
            completion_tokens += getattr(correction, "completion_tokens", 0) or 0
            boundary_blob = _parse_json_object(correction.content)
            boundaries = _validated_boundaries(boundary_blob)
        boundary_reasoning = str(boundary_blob.get("reasoning") or "")
    except Exception as exc:
        boundaries = []
        boundary_reasoning = f"fallback: {type(exc).__name__}: {exc}"
        boundary_fallback = True

    async def generate_segments(cuts: List[int]) -> List[dict]:
        nonlocal prompt_tokens, completion_tokens
        specs, start = [], 1
        for segment_index, end in enumerate(cuts + [len(messages)], 1):
            segment = messages[start - 1:end]
            specs.append((segment_index, start, end, segment))
            start = end + 1

        semaphore = asyncio.Semaphore(max(1, min(16, int(summary_concurrency or 1))))

        async def summarize(spec):
            segment_index, segment_start, segment_end, segment = spec
            response = None
            try:
                async with semaphore:
                    response = await llm_provider.complete(
                        prompt=DYNAMIC_CAPSULE_PROMPT.format(
                            timestamp=timestamp_text,
                            messages=_render_messages(segment),
                        ),
                        max_tokens=4096,
                    )
                blob = _parse_json_object(response.content)
                summary = str(blob.get("summary") or "").strip()
                if not summary:
                    raise ValueError(f"empty summary for segment {segment_index}")
                segment_result = {
                    "start": segment_start, "end": segment_end,
                    "title": str(blob.get("title") or "").strip(),
                    "summary": summary,
                }
                error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                segment_result = None
                error = exc
            return (
                segment_result,
                error,
                getattr(response, "prompt_tokens", 0) or 0,
                getattr(response, "completion_tokens", 0) or 0,
            )

        attempts = await asyncio.gather(*(summarize(spec) for spec in specs))
        result = []
        first_error = None
        for segment_result, error, used_prompt, used_completion in attempts:
            prompt_tokens += used_prompt
            completion_tokens += used_completion
            if error is not None:
                if first_error is None:
                    first_error = error
                continue
            result.append(segment_result)
        if first_error is not None:
            raise first_error
        return result

    try:
        segments = await generate_segments(boundaries)
    except Exception:
        if not boundaries:
            raise
        boundaries = []
        boundary_fallback = True
        boundary_reasoning = "fallback: segmented summary failed; merged complete session"
        segments = await generate_segments([])

    fact_nodes = [
        n for n in nodes
        if n.layer == MemoryLayer.L2_FACT
        and n.status == MemoryStatus.ACTIVE
        and getattr(n, "is_latest", True)
        and (
            n in session_nodes
            or bool(_source_ids(n) & current_raw_ids)
        )
    ]
    fact_embeddings = await vector_store.get_embeddings([n.node_id for n in fact_nodes])
    for segment in segments:
        segment_raw_ids = list(dict.fromkeys(
            message.get("source_raw_memory_id")
            for message in messages[segment["start"] - 1:segment["end"]]
            if message.get("source_raw_memory_id")
        ))
        segment["source_raw_memory_ids"] = segment_raw_ids
    members_by_segment = _assign_facts_to_segments(fact_nodes, segments)
    segments = [
        {**segment, "member_facts": members_by_segment[index]}
        for index, segment in enumerate(segments)
        if members_by_segment[index]
    ]
    if not segments:
        return {
            "status": "skipped", "reason": "no_episode_members",
            "session_id": session_id, "raw_count": len(raw_nodes),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        }
    for segment in segments:
        member_vectors = [
            fact_embeddings[fact.node_id]
            for fact in segment["member_facts"] if fact_embeddings.get(fact.node_id)
        ]
        segment["embedding"] = _normalized_centroid(member_vectors)
        if not segment["embedding"]:
            raise ValueError("episode members have no stored embeddings")

    episode_nodes = []
    for segment in segments:
        episode_id = _dynamic_episode_id(
            user_id, agent_id or "default_agent", session_id, raw_digest,
            segment["start"], segment["end"],
        )
        segment_raw_ids = segment["source_raw_memory_ids"]
        node = MemoryNode(
            node_id=episode_id, user_id=user_id,
            agent_id=agent_id or "default_agent", session_id=session_id,
            source=merge_sources(
                getattr(member, "source", None)
                for member in segment["member_facts"]
            ),
            episode_id=episode_id, layer=MemoryLayer.L3_SUMMARY,
            content=(segment["title"] or segment["summary"][:240]).strip(),
            embedding=segment["embedding"], memory_at=timestamp,
            source_type=SourceType.INFERRED, status=MemoryStatus.ACTIVE,
            is_latest=True,
            custom={
                "summary_kind": SUMMARY_KIND_DYNAMIC_EPISODE,
                "episode_id": episode_id,
                "title": segment["title"],
                "message_start": segment["start"],
                "message_end": segment["end"],
                "source_raw_memory_ids": segment_raw_ids,
                "source_raw_sha256": raw_digest,
                "boundary_fallback": boundary_fallback,
                "boundary_reasoning": boundary_reasoning,
                "embedding_source": "member_l2_centroid",
            },
        )
        await vector_store.upsert(node)
        episode_nodes.append(node)

    linked = 0
    members_by_episode: dict[str, list[str]] = {
        node.node_id: [] for node in episode_nodes
    }
    for episode, segment in zip(episode_nodes, segments):
        for fact in segment["member_facts"]:
            custom = _append_episode_membership(
                fact, episode.node_id, 1.0, "source_raw_lexical",
            )
            if await vector_store.update_payload(
                fact.node_id,
                {
                    "episode_id": _primary_episode_id(fact, episode.node_id),
                    "custom": custom,
                },
            ):
                linked += 1
                members_by_episode.setdefault(episode.node_id, []).append(fact.node_id)
    # Store references, not copied L2 text. The episode is an organization node;
    # answer-visible text is assembled from the referenced L2 memories at read time.
    for episode in episode_nodes:
        member_ids = sorted(set(members_by_episode.get(episode.node_id) or []))
        custom = dict(episode.custom or {})
        custom["member_memory_ids"] = member_ids
        custom["timeline"] = [{
            "memory_at": (
                int(episode.memory_at.timestamp())
                if getattr(episode, "memory_at", None) else None
            ),
            "member_ids": member_ids,
        }]
        await vector_store.update_payload(episode.node_id, {"custom": custom})
        episode.custom = custom
    return {
        "status": "complete", "session_id": session_id,
        "episode_ids": [n.node_id for n in episode_nodes],
        "episode_count": len(episode_nodes), "raw_count": len(raw_nodes),
        "linked_fact_count": linked, "boundary_fallback": boundary_fallback,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
    }


async def finalize_series_episode_containers(
    *, vector_store, graph_store, user_id: str, agent_id: Optional[str],
) -> dict:
    """Create compact multi-session episode containers without an LLM call.

    Stored text is only a title plus a structural intro. Exact L2 bodies remain in
    their canonical nodes and are referenced by ID from dated timeline segments.
    """
    from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus, SourceType
    from ..pipelines._retrieval.session_episode import (
        add_parent_episode_membership,
        remove_parent_episode_memberships,
    )

    nodes = await vector_store.list_by_user(
        user_id=user_id, agent_id=agent_id, limit=10000,
        status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW, MemoryStatus.SUPERSEDED],
        layers=[MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY],
        only_latest=False, with_vectors=False,
    )
    active_l2 = [
        node for node in nodes
        if node.layer == MemoryLayer.L2_FACT
        and node.status == MemoryStatus.ACTIVE
        and bool(getattr(node, "is_latest", True))
    ]
    graph_error = ""
    edges = []
    if graph_store is not None:
        try:
            edges = await graph_store.get_related_edges(
                [node.node_id for node in active_l2],
                max_edges=max(2000, len(active_l2) * 8),
                relation_types=["same_series"],
                include_temporal=False,
            )
        except Exception as exc:
            # Exact cross-session provenance is still authoritative without graph.
            graph_error = str(exc)

    plans = _series_episode_plans(nodes, edges)
    existing = [
        node for node in nodes
        if node.layer == MemoryLayer.L3_SUMMARY
        and (node.custom or {}).get("summary_kind") == SUMMARY_KIND_SERIES_EPISODE
    ]
    desired_ids = {
        _series_episode_id(user_id, agent_id or "default_agent", plan["member_memory_ids"])
        for plan in plans
    }
    existing_ids = {node.node_id for node in existing}
    stale_ids = existing_ids - desired_ids
    for node in existing:
        if node.node_id in stale_ids:
            await vector_store.update_payload(node.node_id, {
                "status": MemoryStatus.SHADOW.value,
                "is_latest": False,
            })

    l2_by_id = {node.node_id: node for node in active_l2}
    # Rebuild all series memberships from the current plans. Limiting cleanup to
    # stale container IDs leaves ghost members behind when a component keeps the
    # same episode set but one L2 is superseded or loses its explicit relation.
    if existing_ids:
        for node in active_l2:
            custom = remove_parent_episode_memberships(node.custom, existing_ids)
            if custom != (node.custom or {}):
                await vector_store.update_payload(node.node_id, {"custom": custom})
                node.custom = custom

    embeddings = await vector_store.get_embeddings([
        member_id
        for plan in plans
        for member_id in plan["member_memory_ids"]
    ])
    created_ids = []
    linked_ids = set()
    for plan in plans:
        series_id = _series_episode_id(
            user_id, agent_id or "default_agent", plan["member_memory_ids"],
        )
        member_vectors = [
            embeddings[member_id]
            for member_id in plan["member_memory_ids"]
            if embeddings.get(member_id)
        ]
        embedding = _normalized_centroid(member_vectors)
        if not embedding:
            continue
        times = [
            segment["memory_at"] for segment in plan["timeline"]
            if segment.get("memory_at") is not None
        ]
        memory_at = _series_memory_at(times)
        content = f"{plan['title']}. {plan['intro']}".strip()
        series = MemoryNode(
            node_id=series_id, user_id=user_id,
            agent_id=agent_id or "default_agent", session_id="",
            source=merge_sources(
                getattr(member, "source", None)
                for member in active_l2
                if member.node_id in set(plan["member_memory_ids"])
            ),
            episode_id=series_id, layer=MemoryLayer.L3_SUMMARY,
            content=content, embedding=embedding, memory_at=memory_at,
            source_type=SourceType.INFERRED, status=MemoryStatus.ACTIVE,
            is_latest=True,
            custom={
                "summary_kind": SUMMARY_KIND_SERIES_EPISODE,
                "episode_id": series_id,
                "title": plan["title"],
                "intro": plan["intro"],
                "source_episode_ids": plan["episode_ids"],
                "source_session_ids": plan["session_ids"],
                "member_memory_ids": plan["member_memory_ids"],
                "timeline": plan["timeline"],
                "relation_evidence": plan["evidence"],
                "embedding_source": "member_l2_centroid",
            },
        )
        await vector_store.upsert(series)
        created_ids.append(series_id)
        for member_id in plan["member_memory_ids"]:
            fact = l2_by_id.get(member_id)
            if fact is None:
                continue
            custom = add_parent_episode_membership(
                fact.custom, series_id, similarity=1.0,
                method="series_episode_explicit",
            )
            if await vector_store.update_payload(member_id, {"custom": custom}):
                fact.custom = custom
                linked_ids.add(member_id)
    return {
        "status": "complete", "created": len(created_ids),
        "episode_ids": created_ids, "linked_fact_count": len(linked_ids),
        "stale_shadowed": len(stale_ids), "prompt_tokens": 0,
        "completion_tokens": 0, "graph_error": graph_error,
    }


def _cosine(a: List[float], b: List[float]) -> float:
    import math
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / den if den else 0.0


async def finalize_session_episode(
    *, vector_store, embed_service, llm_provider, user_id: str,
    agent_id: Optional[str], session_id: str,
) -> dict:
    """Create one first-class narrative from all raw chunks in a caller session.

    This is deliberately explicit: the SDK cannot infer that the final write of a
    session has arrived. Repeated calls upsert the same deterministic node id.
    """
    from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus, SourceType

    if not user_id or not session_id:
        raise ValueError("user_id and session_id are required")
    nodes = await vector_store.list_by_user(
        user_id=user_id, agent_id=agent_id,
        limit=10000,
        status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW, MemoryStatus.SUPERSEDED],
        layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT],
        only_latest=False, with_vectors=False, allow_raw_layer=True,
    )
    eid = session_episode_id(user_id, agent_id or "default_agent", session_id)
    session_nodes = [n for n in nodes if (n.session_id or "default_session") == session_id]
    raw_nodes = [n for n in session_nodes if n.layer == MemoryLayer.L1_RAW]
    raw_nodes.sort(key=lambda n: (n.memory_at or n.gmt_created, n.gmt_created, n.node_id))
    # A runner can be interrupted after the service commits a chunk but before its
    # local checkpoint advances. Resume then sends the same raw conversation again
    # under a new node id. Collapse exact content replays before prompting, so retry
    # recovery does not multiply the episode narrative.
    unique_raw_nodes = []
    seen_raw = set()
    for node in raw_nodes:
        normalized = re.sub(r"\s+", " ", (node.content or "").strip())
        if not normalized or normalized in seen_raw:
            continue
        seen_raw.add(normalized)
        unique_raw_nodes.append(node)
    raw_nodes = unique_raw_nodes
    if not raw_nodes:
        return {"status": "skipped", "reason": "no_raw_memories", "session_id": session_id}

    raw_ids = [n.node_id for n in raw_nodes]
    raw_contents = [
        re.sub(r"\s+", " ", (n.content or "").strip()) for n in raw_nodes
    ]
    # Content-based digest remains stable when a retried chunk receives a new node id.
    raw_digest = hashlib.sha256("\0".join(raw_contents).encode("utf-8")).hexdigest()
    fact_nodes = [n for n in nodes
                  if n.layer == MemoryLayer.L2_FACT
                  and n.status == MemoryStatus.ACTIVE
                  and getattr(n, "is_latest", True)
                  and (
                      n.episode_id == eid
                      or n in session_nodes
                      or bool(_source_ids(n) & set(raw_ids))
                  )]
    existing = await vector_store.get_by_id(eid)
    if existing is not None and (getattr(existing, "custom", None) or {}).get(
        "source_raw_sha256"
    ) == raw_digest:
        linked = 0
        for node in fact_nodes:
            custom = _append_episode_membership(
                node, eid, 1.0, "source_raw_membership",
            )
            if await vector_store.update_payload(
                node.node_id,
                {"episode_id": _primary_episode_id(node, eid), "custom": custom},
            ):
                linked += 1
        custom = dict(existing.custom or {})
        custom["linked_fact_count"] = linked
        await vector_store.update_payload(eid, {"custom": custom})
        return {
            "status": "reused", "session_id": session_id,
            "episode_id": eid, "episode_memory_id": eid,
            "raw_count": len(raw_nodes), "linked_fact_count": linked,
            "prompt_tokens": 0, "completion_tokens": 0,
        }

    conversation = "\n\n".join((n.content or "").strip() for n in raw_nodes if (n.content or "").strip())
    timestamp = min((n.memory_at for n in raw_nodes if n.memory_at), default=None)
    timestamp_text = (
        timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "unknown"
    )
    prompt = SESSION_EPISODE_PROMPT.format(
        timestamp=timestamp_text,
        conversation=conversation,
    )
    response = await llm_provider.complete(prompt=prompt, max_tokens=3000)
    narrative = (getattr(response, "content", "") or "").strip()
    if not narrative:
        raise RuntimeError("session episode generation returned empty content")
    tags: List[str] = []
    for node in fact_nodes:
        for tag in node.tags or []:
            if tag and tag not in tags:
                tags.append(tag)
    embedding = await embed_service.embed_queued(narrative)
    episode = MemoryNode(
        node_id=eid, user_id=user_id, agent_id=agent_id or "default_agent",
        session_id=session_id, episode_id=eid, layer=MemoryLayer.L3_SUMMARY,
        source=merge_sources(
            getattr(node, "source", None) for node in [*raw_nodes, *fact_nodes]
        ),
        content=narrative, embedding=embedding, memory_at=timestamp,
        source_type=SourceType.INFERRED, status=MemoryStatus.ACTIVE,
        is_latest=True, tags=tags[:8],
        custom={
            "summary_kind": SUMMARY_KIND_SESSION_EPISODE,
            "episode_id": eid,
            "source_raw_memory_ids": raw_ids,
            "source_raw_sha256": raw_digest,
            "covered_raw_count": len(raw_nodes),
            "linked_fact_count": len(fact_nodes),
        },
    )
    await vector_store.upsert(episode)
    linked = 0
    for node in fact_nodes:
        custom = _append_episode_membership(
            node, eid, 1.0, "source_raw_membership",
        )
        if await vector_store.update_payload(
            node.node_id,
            {"episode_id": _primary_episode_id(node, eid), "custom": custom},
        ):
            linked += 1
    return {
        "status": "complete", "session_id": session_id,
        "episode_id": eid, "episode_memory_id": eid,
        "raw_count": len(raw_nodes), "linked_fact_count": linked,
        "prompt_tokens": getattr(response, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(response, "completion_tokens", 0) or 0,
    }


async def summarize_closed_episode(
    *,
    vector_store,
    embed_service,
    llm_provider,
    episode_id: str,
    user_id: str,
    agent_id: Optional[str] = None,
    session_id: str = "",
    min_memories: int = 3,
    max_memories: int = 60,
    temperature: Optional[float] = None,
    trace=None,
) -> Optional[str]:
    """总结一个**刚刚关闭**的 episode，返回摘要节点 id（跳过则 None）。

    由 writer 在检测到 episode 滚动时 fire-and-forget 调用：此时上一个 episode
    不会再有新内容进来，所以这一次总结就是最终产物，不需要后续 merge / 重写。
    """
    if not episode_id or not user_id:
        return None
    try:
        from ..models.memory import MemoryLayer

        nodes = await vector_store.list_by_episode(
            [episode_id],
            user_id=user_id,
            agent_id=agent_id,
            layers=[MemoryLayer.L2_FACT, MemoryLayer.L7_INTENTION],
            limit=max_memories,
        )
    except Exception as e:
        logger.debug(f"[episode-summary] fetch members failed: {e}")
        return None

    # 只喂 user 侧记忆：agent 侧记忆（助手说过什么）对用户画像价值低，喂进去纯是烧 token。
    # 与 user-turn 计数口径一致，episode 只统计 user 侧记忆。
    # owner 缺省视为 user（与落库时的默认一致）。
    nodes = [
        n for n in (nodes or [])
        if (getattr(n, "owner", None) or "user") == "user"
    ]
    contents = [
        (getattr(n, "content", "") or "").strip()
        for n in nodes
        if (getattr(n, "content", "") or "").strip()
    ]
    if len(contents) < max(2, min_memories):
        # 一两条记忆的 episode 不值得单独摘要 —— 原记忆本身就够检索了
        return None

    from datetime import datetime as _dt

    _t0 = _dt.now()
    summary, prompt, tokens = await _generate(
        llm_provider=llm_provider, memories=contents, temperature=temperature,
    )
    _elapsed = (_dt.now() - _t0).total_seconds() * 1000
    if trace is not None:
        await trace(
            prompt=prompt,
            response=summary,
            parsed={
                "episode_id": episode_id,
                "covered_count": len(contents),
                "skipped": not summary,
            },
            memory_ids=[n.node_id for n in nodes if getattr(n, "node_id", "")],
            elapsed_ms=_elapsed,
            tokens=tokens,
        )
    if not summary:
        return None

    return await _store(
        vector_store=vector_store,
        embed_service=embed_service,
        summary=summary,
        episode_id=episode_id,
        source_memory_ids=[n.node_id for n in nodes if getattr(n, "node_id", "")],
        covered_count=len(contents),
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        nodes=nodes,
    )


async def _generate(
    *, llm_provider, memories: List[str], temperature: Optional[float] = None,
) -> tuple:
    """返回 (摘要文本, 实际 prompt, token 用量) —— 后两个供埋点，线上要能复盘。"""
    from ..utils.lang_detect import is_chinese

    body = "\n".join(f"- {c}" for c in memories if c)
    if not body:
        return "", "", {}
    tpl = EPISODE_SUMMARY_PROMPT_ZH if is_chinese(body) else EPISODE_SUMMARY_PROMPT
    prompt = tpl.format(memories=body)
    resp = await llm_provider.complete(
        prompt=prompt,
        max_tokens=400,
        temperature=temperature,
    )
    tokens = {
        "prompt": getattr(resp, "prompt_tokens", 0) or 0,
        "completion": getattr(resp, "completion_tokens", 0) or 0,
        "total": getattr(resp, "total_tokens", 0) or 0,
    }
    text = (getattr(resp, "content", "") or "").strip()
    # LLM 偶尔会包一层 markdown 围栏或引号
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        text = text.strip()
    return text.strip().strip('"').strip("'").strip(), prompt, tokens


async def _store(
    *,
    vector_store,
    embed_service,
    summary: str,
    episode_id: str,
    source_memory_ids: List[str],
    covered_count: int,
    user_id: str,
    agent_id: Optional[str],
    session_id: str,
    nodes: List[Any],
) -> str:
    from datetime import datetime

    from ..models.memory import (
        MemoryLayer, MemoryNode, MemoryStatus, SourceType,
    )

    # 摘要的事件时间取该 episode 最后一条记忆的时间，让时间过滤把它归到讨论发生的时点，
    # 而不是"总结这一刻"。
    memory_at = datetime.now()
    times = [
        getattr(n, "memory_at", None) for n in (nodes or [])
        if getattr(n, "memory_at", None)
    ]
    if times:
        memory_at = max(times)

    # 沿用 episode 内记忆的 tag，让主题词也能召回到这条摘要
    tags: List[str] = []
    for n in (nodes or []):
        for t in (getattr(n, "tags", None) or []):
            if t and t not in tags:
                tags.append(t)

    embedding = await embed_service.embed_queued(summary)
    node = MemoryNode(
        user_id=user_id,
        agent_id=agent_id or "default_agent",
        session_id=session_id or "default_session",
        source=merge_sources(getattr(n, "source", None) for n in (nodes or [])),
        episode_id=episode_id,
        layer=MemoryLayer.L3_SUMMARY,
        content=summary,
        embedding=embedding,
        memory_at=memory_at,
        source_type=SourceType.INFERRED,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        tags=tags[:8],
        custom={
            "summary_kind": SUMMARY_KIND_EPISODE,
            "episode_id": episode_id,
            "covered_count": covered_count,
            "source_memory_ids": list(source_memory_ids),
        },
    )
    # 一个 episode 只总结一次 → 没有旧摘要要 SHADOW，也不进 supersedes 演化链
    return await vector_store.upsert(node)
