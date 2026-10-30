"""
System 2 Agent — 统一认知加工引擎

替代旧的 SchemaInductor + IntentionDetector，改为单一 Agent + Tools 架构。

流程:
  Phase 1 (硬编码预处理):
    ① VDB 取未处理的 L2_FACT + L4_IDENTITY
    ② DBSCAN 聚类 → clusters
    ③ Graph 正向搜索: 按 cluster 主题 query Graph
    ④ Graph 反向查找: VDB node_id → find_referencing_memories

  Phase 2 (Agent LLM，仅当 clusters_found > 0):
    输入: 聚类结果为主；unprocessed_facts = 未进任一 cluster 的 fresh facts（加菜，有上限）
    无聚类时不触发 Agent（减轻冷启动/散事实场景的 LLM 负担）

  工具: 8 个 tools — 演化高级认知 (Schema + Intent + 关系)
"""

import json
import logging
import os
import time
import re
import uuid
from typing import Any, Dict, List, Optional
from types import SimpleNamespace

from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus, SourceType
from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store_base import VectorStoreBase
from ..data.graph_store_base import GraphStoreBase

from .system2_tools import System2ToolExecutor
from .specific_supplement import (
    is_internal_profile_chain_custom,
    is_internal_profile_chain_hit,
)

logger = logging.getLogger(__name__)

def _evoling_env(name: str, default: str) -> str:
    """Read a System 2 Evoling setting."""
    return os.getenv(f"MEMORY_EVOLING_{name}", default)


_S2_SUMMARY_ENABLED = os.getenv("MEMORY_SUMMARY_ENABLED_IN_SYS2", "false").lower() == "true"
# 有聚类时，未聚类事实作为加菜；超过此数量截断，避免 1 cluster + 30 条 noise
_S2_MAX_UNCLUSTERED_FACTS = int(os.getenv("MEMORY_S2_MAX_UNCLUSTERED_FACTS", "15"))
# Bound the number of candidate facts sent in one System 2 call. A semantic
# cluster can legitimately span most of a long-lived user's bank; asking the LLM
# to emit one decision per fact then exhausts its output budget and leaves an
# unparseable partial JSON array. The writer rescans after every successful batch,
# so reviewing a deterministic slice is a safe checkpoint and the remaining facts
# are picked up by the next iteration/cycle.
_S2_MAX_FACTS_PER_BATCH = int(_evoling_env("MAX_FACTS_PER_BATCH", "24"))
# 簇内 embedding 余弦相似度 ≥ 此阈值视为重复，只保留一条（防系统重复写入闯关）
_S2_CLUSTER_DEDUP_COSINE = float(os.getenv("MEMORY_S2_CLUSTER_DEDUP_COSINE", "0.92"))
_S2_SOURCE_GROUPS_ENABLED = _evoling_env(
    "SOURCE_FACTS_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_SOURCE_GROUP_MIN_FACTS = int(_evoling_env(
    "SOURCE_GROUP_MIN_FACTS", "1",
))
_S2_SOURCE_GROUP_MAX_FACTS = int(_evoling_env(
    "SOURCE_GROUP_MAX_FACTS", "12",
))
_S2_SOURCE_RAW_MAX_CHARS = int(_evoling_env(
    "SOURCE_RAW_MAX_CHARS", "24000",
))
_S2_FORGET_ENABLED = _evoling_env(
    "FORGET_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_CANONICAL_STATE_ENABLED = _evoling_env(
    "CANONICAL_STATE_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_CANONICAL_STATE_REVIEW_VERSION = 3
_S2_CANONICAL_STATE_VERSION = 2
_S2_SCHEMA_EVIDENCE_GATE_ENABLED = _evoling_env(
    "SCHEMA_EVIDENCE_GATE_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_HOLISTIC_THERAPY_ENABLED = _evoling_env(
    "HOLISTIC_THERAPY_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_HOLISTIC_THERAPY_MAX_FACTS = int(_evoling_env(
    "HOLISTIC_THERAPY_MAX_FACTS", "120",
))
_S2_HOLISTIC_THERAPY_MAX_SCHEMAS = int(_evoling_env(
    "HOLISTIC_THERAPY_MAX_SCHEMAS", "5",
))
_S2_PROFILE_FACT_PROMOTION_ENABLED = _evoling_env(
    "PROFILE_FACT_PROMOTION_ENABLED", "false",
).lower() in ("1", "true", "yes", "on")
_S2_PROFILE_FACT_PROMOTION_MAX_FACTS = int(_evoling_env(
    "PROFILE_FACT_PROMOTION_MAX_FACTS", "120",
))
_S2_PROFILE_FACT_PROMOTION_MAX_SELECTED = int(_evoling_env(
    "PROFILE_FACT_PROMOTION_MAX_SELECTED", "12",
))
_S2_PROFILE_FACT_SOURCE_CONTEXT_MAX_CHARS = int(_evoling_env(
    "PROFILE_FACT_SOURCE_CONTEXT_MAX_CHARS", "48000",
))
_S2_PROFILE_FACT_PROMOTION_REVIEW_VERSION = 2

_EQUIPMENT_SPECS = {
    "glasses": {
        "aliases": ("glasses", "eyeglasses", "spectacles", "prescription glasses"),
        "state_key": "assistive_equipment.glasses",
        "state_value": "uses",
        "content": "The user uses glasses for visual detail.",
        "retrieval_text": (
            "Personal eyewear state: the user uses glasses for visual detail. "
            "Relevant contexts: cinema and theater viewing, 3D glasses compatibility, "
            "screen work, reading, eye comfort, and visual accessibility."
        ),
        "tags": ["health", "vision", "eyewear"],
    },
    "hearing_aids": {
        "aliases": ("hearing aids", "hearing aid"),
        "state_key": "assistive_equipment.hearing_aids",
        "state_value": "uses",
        "content": "The user uses hearing aids.",
        "retrieval_text": (
            "Hearing support: the user uses hearing aids; relevant to listening "
            "conditions, communication, and noisy environments."
        ),
        "tags": ["health", "hearing", "assistive-equipment"],
    },
    "cpap": {
        "aliases": ("cpap", "cpap machine", "cpap device"),
        "state_key": "assistive_equipment.cpap",
        "state_value": "uses",
        "content": "The user uses a CPAP device.",
        "retrieval_text": (
            "Sleep and breathing support: the user uses a CPAP device; relevant "
            "to sleep routines, overnight stays, and travel."
        ),
        "tags": ["health", "sleep", "assistive-equipment"],
    },
    "knee_brace": {
        "aliases": ("knee brace",),
        "state_key": "assistive_equipment.knee_brace",
        "state_value": "uses",
        "content": "The user uses a knee brace.",
        "retrieval_text": (
            "Mobility support: the user uses a knee brace; relevant to walking, "
            "hiking, exercise, and physical comfort."
        ),
        "tags": ["health", "mobility", "assistive-equipment"],
    },
    "mobility_aid": {
        "aliases": ("wheelchair", "walker", "walking stick", "cane", "crutches"),
        "state_key_template": "assistive_equipment.{equipment}",
        "state_value": "uses",
        "content_template": "The user uses {article}{equipment} as a mobility aid.",
        "retrieval_text_template": (
            "Mobility support: the user uses {article}{equipment}; relevant to "
            "accessibility, walking, travel, and physical activities."
        ),
        "tags": ["health", "mobility", "assistive-equipment"],
    },
}

_EQUIPMENT_USE_VERBS = (
    r"wear(?:ing|s)?|use(?:d|s|ing)?|adjust(?:ed|ing|s)?|"
    r"put(?:ting)?\s+on|rely(?:ing|ies|ied)?\s+on|need(?:ed|s|ing)?|"
    r"pack(?:ed|ing|s)?|bring(?:ing|s)?|brought"
)
_EQUIPMENT_REJECT_RE = re.compile(
    r"\b(?:borrow(?:ed|ing)?|lend|lent|rented?|trying\s+out|tried\s+on|"
    r"costume|as\s+a\s+joke|for\s+a\s+joke|temporar(?:y|ily)|one[- ]time|"
    r"if\s+i|would\s+i|could\s+i|should\s+i|might\s+i|"
    r"do\s+not|don't|never|no\s+longer|stopped)\b",
    re.I,
)


def _iter_role_messages(raw_text: str) -> List[Dict[str, str]]:
    """Parse the stable L1 ``[user]:`` / ``[assistant]:`` envelope."""
    text = str(raw_text or "")
    matches = list(re.finditer(r"(?m)^\[(user|assistant)\]:\s*", text))
    messages: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        messages.append({"role": match.group(1), "text": text[match.end():end].strip()})
    return messages


def _equipment_mentioned(text: str, aliases: tuple[str, ...]) -> Optional[str]:
    lowered = str(text or "").lower()
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered):
            return alias
    return None


def _is_explicit_personal_equipment_use(text: str, alias: str) -> bool:
    """Require first-person possession/use and reject questions or temporary use."""
    value = " ".join(str(text or "").split())
    lowered = value.lower()
    if not value or _EQUIPMENT_REJECT_RE.search(lowered):
        return False
    if re.search(r"\b(?:please\s+)?forget\s+(?:that\s+)?(?:i\b|i'm\b|i’ve\b|i've\b|my\b|me\b)", lowered):
        return False
    if value.endswith("?") or re.match(
        r"^(?:what|which|who|when|where|why|how|can|could|should|would|do|does|is|are|will)\b",
        lowered,
    ):
        return False
    equipment = re.escape(alias.lower())
    direct_patterns = (
        rf"\bi\s+(?:\w+\s+){{0,3}}(?:{_EQUIPMENT_USE_VERBS})\s+(?:my\s+|a\s+|an\s+)?{equipment}\b",
        rf"\b(?:{_EQUIPMENT_USE_VERBS})\s+my\s+{equipment}\b",
        rf"\bmy\s+{equipment}\b.{0,50}\b(?:{_EQUIPMENT_USE_VERBS})\b",
    )
    return any(re.search(pattern, lowered, re.I) for pattern in direct_patterns)


def extract_personal_equipment_states(raw_text: str) -> List[Dict[str, Any]]:
    """Extract a narrow class of explicit, durable equipment-use states."""
    states: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for message in _iter_role_messages(raw_text):
        if message["role"] != "user":
            continue
        utterance = message["text"]
        for kind, spec in _EQUIPMENT_SPECS.items():
            alias = _equipment_mentioned(utterance, spec["aliases"])
            if not alias or kind in seen:
                continue
            if not _is_explicit_personal_equipment_use(utterance, alias):
                continue
            article = "an " if alias[:1].lower() in "aeiou" else "a "
            content = spec.get("content") or spec["content_template"].format(
                equipment=alias, article=article,
            )
            retrieval_text = spec.get("retrieval_text") or spec["retrieval_text_template"].format(
                equipment=alias, article=article,
            )
            states.append({
                "equipment_kind": kind,
                "equipment": alias,
                "state_key": spec.get("state_key") or spec["state_key_template"].format(
                    equipment=alias.replace(" ", "_"),
                ),
                "state_value": spec["state_value"],
                "content": content,
                "retrieval_text": retrieval_text,
                "tags": list(spec["tags"]),
                "evidence_quote": utterance,
            })
            seen.add(kind)
    return states


def build_canonical_state_reviews(
    raw_nodes: List[Any],
    *,
    facts: List[Any],
    active_schemas: List[Any],
) -> List[Dict[str, Any]]:
    """Build canonical-state reviews from raw evidence.

    Ordinary event facts and behavioral Schemas do not count as the canonical
    state record. Exact deduplication happens later by ``state_key``.
    """
    del facts, active_schemas

    def _event_time(node: Any) -> float:
        value = getattr(node, "memory_at", None) or getattr(node, "gmt_created", None)
        if hasattr(value, "timestamp"):
            return float(value.timestamp())
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    reviews: List[Dict[str, Any]] = []
    for raw_node in sorted(raw_nodes, key=_event_time):
        custom = dict(getattr(raw_node, "custom", None) or {})
        if int(custom.get("s2_canonical_state_review_version") or 0) >= _S2_CANONICAL_STATE_REVIEW_VERSION:
            continue
        candidates = []
        for state in extract_personal_equipment_states(getattr(raw_node, "content", "")):
            kind = state["equipment_kind"]
            candidates.append({**state, "source_raw_memory_id": str(raw_node.node_id)})
        retire_state_keys = []
        for directive in extract_raw_forget_directives(getattr(raw_node, "content", "")):
            for kind, spec in _EQUIPMENT_SPECS.items():
                if _equipment_mentioned(directive, spec["aliases"]):
                    retire_state_keys.append(
                        spec.get("state_key") or spec["state_key_template"].format(
                            equipment=_equipment_mentioned(directive, spec["aliases"]).replace(" ", "_")
                        )
                    )
        reviews.append({
            "node_id": str(raw_node.node_id),
            "candidates": candidates,
            "retire_state_keys": list(dict.fromkeys(retire_state_keys)),
        })
    return reviews


def canonical_state_node_id(
    user_id: str,
    agent_id: str,
    state_key: str,
) -> str:
    """One stable materialized state record per user, agent, and semantic key."""
    identity = f"canonical_state::{user_id}::{agent_id}::{state_key}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


async def run_canonical_state_projection(
    *,
    reviews: List[Dict[str, Any]],
    tool_executor: System2ToolExecutor,
) -> Dict[str, Any]:
    """Project explicit raw evidence into versioned, canonical L2 state facts."""
    if not _S2_CANONICAL_STATE_ENABLED or not reviews:
        return {
            "success": True,
            "enabled": _S2_CANONICAL_STATE_ENABLED,
            "reviews": 0,
            "created": 0,
            "reviewed_raw_ids": [],
            "failed_raw_ids": [],
            "tool_calls": [],
        }

    stored_states = await tool_executor.vector_store.list_by_user(
        user_id=tool_executor.user_id,
        agent_id=tool_executor.agent_id,
        layers=[MemoryLayer.L2_FACT],
        only_latest=False,
        with_vectors=False,
    )
    by_key = {
        str((node.custom or {}).get("state_key") or ""): node
        for node in stored_states
        if (node.custom or {}).get("memory_kind") == "canonical_state"
        and (node.custom or {}).get("state_key")
    }

    created = 0
    merged = 0
    superseded = 0
    retired = 0
    reviewed_raw_ids: List[str] = []
    failed_raw_ids: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for review in reviews:
        raw_id = str(review.get("node_id") or "")
        raw_node = await tool_executor.vector_store.get_by_id(raw_id)
        if (
            raw_node is None
            or raw_node.user_id != tool_executor.user_id
            or raw_node.agent_id != tool_executor.agent_id
            or raw_node.layer != MemoryLayer.L1_RAW
        ):
            failed_raw_ids.append(raw_id)
            continue
        raw_succeeded = True
        for state_key in review.get("retire_state_keys") or []:
            current = by_key.get(state_key)
            if current is None or current.status != MemoryStatus.ACTIVE:
                continue
            custom = dict(current.custom or {})
            history = list(custom.get("state_history") or [])
            history.append({
                "state_value": custom.get("state_value"),
                "content": current.content,
                "evidence_raw_ids": list(
                    custom.get("evidence_raw_ids") or current.evidence_chain or []
                ),
                "valid_until": int(time.time()),
                "retired_by_raw_id": raw_id,
            })
            custom["state_history"] = history
            custom["retired_by_raw_id"] = raw_id
            updated = await tool_executor.vector_store.update_payload(current.node_id, {
                "status": MemoryStatus.ARCHIVED.value,
                "is_latest": False,
                "valid_until": int(time.time()),
                "custom": custom,
            })
            if not updated:
                raw_succeeded = False
                break
            current.status = MemoryStatus.ARCHIVED
            current.custom = custom
            retired += 1
            tool_calls.append({
                "op": "state_retire", "node_id": current.node_id,
                "state_key": state_key, "source_raw_memory_id": raw_id,
            })
        if not raw_succeeded:
            failed_raw_ids.append(raw_id)
            continue
        for candidate in review.get("candidates") or []:
            state_key = candidate["state_key"]
            state_value = candidate["state_value"]
            current = by_key.get(state_key)
            if (
                current is not None
                and current.status == MemoryStatus.ACTIVE
                and str((current.custom or {}).get("state_value")) == state_value
            ):
                custom = dict(current.custom or {})
                evidence_ids = list(custom.get("evidence_raw_ids") or current.evidence_chain or [])
                if raw_id not in evidence_ids:
                    evidence_ids.append(raw_id)
                custom["evidence_raw_ids"] = evidence_ids
                needs_refresh = int(
                    custom.get("state_projection_version") or 0
                ) < _S2_CANONICAL_STATE_VERSION
                custom["state_projection_version"] = _S2_CANONICAL_STATE_VERSION
                custom["state_retrieval_text"] = candidate["retrieval_text"]
                updates = {
                    "custom": custom,
                    "evidence_chain": evidence_ids,
                    "evidence_count": len(evidence_ids),
                }
                from ..utils.source_filter import merge_sources
                evidence_nodes = await tool_executor.vector_store.get_by_ids(evidence_ids)
                updates["source"] = merge_sources(
                    getattr(evidence_node, "source", None)
                    for evidence_node in evidence_nodes
                )
                if needs_refresh:
                    updates.update({
                        "content": candidate["content"],
                        "embedding": await tool_executor.embed_service.embed_queued(
                            candidate["retrieval_text"]
                        ),
                        "tags": list(candidate["tags"]),
                        "gmt_modified": int(time.time()),
                    })
                updated = await tool_executor.vector_store.update_payload(
                    current.node_id, updates,
                )
                if not updated:
                    raw_succeeded = False
                    break
                current.custom = custom
                current.evidence_chain = evidence_ids
                current.evidence_count = len(evidence_ids)
                if needs_refresh:
                    current.content = candidate["content"]
                merged += 1
                continue

            if current is not None:
                old_custom = dict(current.custom or {})
                history = list(old_custom.get("state_history") or [])
                history.append({
                    "state_value": old_custom.get("state_value"),
                    "content": current.content,
                    "evidence_raw_ids": list(
                        old_custom.get("evidence_raw_ids") or current.evidence_chain or []
                    ),
                    "valid_until": int(time.time()),
                })
                new_custom = {
                    **old_custom,
                    "memory_kind": "canonical_state",
                    "state_key": state_key,
                    "state_value": state_value,
                    "state_retrieval_text": candidate["retrieval_text"],
                    "evidence_raw_ids": [raw_id],
                    "state_evidence_quote": candidate["evidence_quote"],
                    "state_history": history,
                    "s2_reviewed_count": 1,
                }
                updated = await tool_executor.vector_store.update_payload(current.node_id, {
                    "content": candidate["content"],
                    "source": getattr(raw_node, "source", "default") or "default",
                    "embedding": await tool_executor.embed_service.embed_queued(
                        candidate["retrieval_text"]
                    ),
                    "tags": list(candidate["tags"]),
                    "memory_at": (
                        int(raw_node.memory_at.timestamp())
                        if getattr(raw_node, "memory_at", None) else None
                    ),
                    "source_raw_memory_id": raw_id,
                    "evidence_chain": [raw_id],
                    "evidence_count": 1,
                    "status": MemoryStatus.ACTIVE.value,
                    "is_latest": True,
                    "gmt_modified": int(time.time()),
                    "custom": new_custom,
                })
                if not updated:
                    raw_succeeded = False
                    break
                current.content = candidate["content"]
                current.custom = new_custom
                current.evidence_chain = [raw_id]
                current.evidence_count = 1
                current.status = MemoryStatus.ACTIVE
                superseded += 1
                tool_calls.append({
                    "op": "state_update", "node_id": current.node_id,
                    "state_key": state_key, "state_value": state_value,
                    "source_raw_memory_id": raw_id,
                })
                continue

            state_custom = {
                "memory_kind": "canonical_state",
                "state_key": state_key,
                "state_value": state_value,
                "state_retrieval_text": candidate["retrieval_text"],
                "evidence_raw_ids": [raw_id],
                "state_evidence_quote": candidate["evidence_quote"],
                "state_projection_version": _S2_CANONICAL_STATE_VERSION,
                "s2_reviewed_count": 1,
            }
            node = MemoryNode(
                node_id=canonical_state_node_id(
                    tool_executor.user_id,
                    tool_executor.agent_id,
                    state_key,
                ),
                user_id=tool_executor.user_id,
                agent_id=tool_executor.agent_id,
                session_id=getattr(raw_node, "session_id", "") or "default_session",
                source=getattr(raw_node, "source", "default") or "default",
                layer=MemoryLayer.L2_FACT,
                content=candidate["content"],
                owner="user",
                source_type=SourceType.EXPLICIT,
                status=MemoryStatus.ACTIVE,
                confidence=0.98,
                evidence_count=1,
                memory_at=getattr(raw_node, "memory_at", None),
                source_raw_memory_id=raw_id,
                evidence_chain=[raw_id],
                tags=list(candidate["tags"]),
                custom=state_custom,
                embedding=await tool_executor.embed_service.embed_queued(
                    candidate["retrieval_text"]
                ),
            )
            try:
                await tool_executor.vector_store.upsert(node)
            except Exception as exc:
                tool_calls.append({"op": "state_create", "state_key": state_key, "error": str(exc)})
                raw_succeeded = False
                break
            by_key[state_key] = node
            tool_calls.append({
                "op": "state_create",
                "node_id": node.node_id,
                "state_key": state_key,
                "state_value": state_value,
                "source_raw_memory_id": raw_id,
            })
            created += 1
        if not raw_succeeded:
            failed_raw_ids.append(raw_id)
            continue
        custom = dict(raw_node.custom or {})
        custom["s2_canonical_state_review_version"] = _S2_CANONICAL_STATE_REVIEW_VERSION
        try:
            await tool_executor.vector_store.update_payload(raw_id, {"custom": custom})
            reviewed_raw_ids.append(raw_id)
        except Exception:
            failed_raw_ids.append(raw_id)

    return {
        "success": not failed_raw_ids,
        "enabled": True,
        "reviews": len(reviews),
        "created": created,
        "merged": merged,
        "superseded": superseded,
        "retired": retired,
        "reviewed_raw_ids": reviewed_raw_ids,
        "failed_raw_ids": failed_raw_ids,
        "tool_calls": tool_calls,
    }

_FORGET_EN_RE = re.compile(
    r"\b(?:asked|told|requested)\b.{0,80}\bforget\s+"
    r"(?:that\s+(?:they|the user)\b|(?:their|the user's)\b)",
    re.I,
)
_FORGET_ZH_RE = re.compile(r"用户.{0,30}(?:请|要求|让).{0,20}忘记.{0,10}(?:自己|其|他们|她|他)")


def is_explicit_forget_marker(text: str) -> bool:
    """Conservative detector for user-scoped, explicit forgetting facts."""
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if not (lowered.startswith("the user ") or value.startswith("用户")):
        return False
    return bool(_FORGET_EN_RE.search(value) or _FORGET_ZH_RE.search(value))


def extract_raw_forget_directives(raw_text: str) -> List[str]:
    """Extract explicit self-directed forgetting requests from L1 raw text.

    Only user turns are considered.  The object must be first-person (I/my/me or
    Chinese self-reference); requests about another named person are excluded.
    """
    text = str(raw_text or "")
    messages = list(re.finditer(r"(?m)^\[(user|assistant)\]:\s*", text))
    directives: List[str] = []
    for index, match in enumerate(messages):
        if match.group(1) != "user":
            continue
        end = messages[index + 1].start() if index + 1 < len(messages) else len(text)
        utterance = text[match.end():end].strip()
        lowered = utterance.lower()
        english = re.search(
            r"\b(?:please\s+)?forget\s+(?:that\s+)?(?:i\b|i'm\b|i’ve\b|i've\b|my\b|me\b)",
            lowered,
        )
        chinese = re.search(r"(?:请)?忘记.{0,12}(?:我|我的|本人)", utterance)
        if english or chinese:
            directives.append(utterance)
    return directives


def _text_terms(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]+", (text or "").lower())
        if len(token) >= 3
    }


def select_relevant_raw_excerpt(candidate_text: str, raw_text: str, max_chars: int = 6000) -> str:
    """Select the most relevant message plus an adjacent dialogue turn.

    L1 writes can contain many unrelated conversations. Passing the entire write
    caused the model to overlook one candidate or infer a pattern from a different
    turn. Lexical overlap is deterministic and only selects context; it does not
    decide whether a Schema should be created.
    """
    text = str(raw_text or "").strip()
    if not text:
        return ""
    matches = list(re.finditer(r"(?m)^\[(user|assistant)\]:\s*", text))
    if not matches:
        return text[:max_chars]
    messages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        messages.append({
            "role": match.group(1),
            "text": text[match.end():end].strip(),
        })
    candidate_terms = _text_terms(candidate_text)
    best_index = max(
        range(len(messages)),
        key=lambda idx: len(candidate_terms & _text_terms(messages[idx]["text"])),
    )
    selected = {best_index}
    if messages[best_index]["role"] == "assistant" and best_index > 0:
        selected.add(best_index - 1)
    elif messages[best_index]["role"] == "user" and best_index + 1 < len(messages):
        selected.add(best_index + 1)
    excerpt = "\n".join(
        f"[{messages[index]['role']}]: {messages[index]['text']}"
        for index in sorted(selected)
    )
    return excerpt[:max_chars]


def build_evoling_batches(
    materials: Dict[str, Any],
    *,
    max_batches: int,
) -> List[Dict[str, Any]]:
    """Split one preprocessing snapshot into bounded, one-cluster LLM batches.

    The previous implementation asked one response to describe every cluster and
    regularly hit the output-token ceiling. A batch deliberately contains at most
    ``MEMORY_EVOLING_MAX_FACTS_PER_BATCH`` facts from one cluster and no unrelated
    unclustered supplement. Existing graph context is retained so the model can
    attach evidence instead of creating duplicates. Fact order is preserved so a
    successful slice can be checkpointed and the next rescan deterministically
    advances to the remaining facts.
    """
    clusters = list(materials.get("clusters") or [])
    limit = max(0, int(max_batches))
    max_facts = max(1, _S2_MAX_FACTS_PER_BATCH)
    planned: List[Dict[str, Any]] = []
    for cluster in clusters:
        facts = list(cluster.get("facts") or [])
        slices = [facts[i:i + max_facts] for i in range(0, len(facts), max_facts)] or [[]]
        for fact_slice in slices:
            selected_ids = {
                str(fact.get("node_id") or "") for fact in fact_slice
                if fact.get("node_id")
            }
            batch_cluster = {
                **cluster,
                "ids": [
                    node_id for node_id in (cluster.get("ids") or [])
                    if str(node_id) in selected_ids
                ],
                "facts": fact_slice,
            }
            if cluster.get("raw_contexts"):
                batch_cluster["raw_contexts"] = [
                    item for item in cluster.get("raw_contexts") or []
                    if str(item.get("node_id") or "") in selected_ids
                ]
            stats = dict(materials.get("stats") or {})
            stats.update({
                "clusters_found": 1,
                "clustered_facts_count": len(fact_slice),
                "unclustered_facts_count": 0,
                "evoling_batch_index": len(planned),
            })
            planned.append({
                **materials,
                "clusters": [batch_cluster],
                "graph_forward": cluster.get(
                    "graph_forward", materials.get("graph_forward", [])
                ),
                "unprocessed_facts": [],
                "stats": stats,
            })
    batches = planned[:limit]
    for batch in batches:
        batch["stats"]["evoling_batch_count"] = len(batches)
    return batches


def cluster_fingerprint(cluster: Dict[str, Any]) -> str:
    """Stable identity for continuation/no-progress accounting."""
    return "|".join(sorted(
        str(fact.get("node_id") or "")
        for fact in cluster.get("facts") or []
        if fact.get("node_id")
    ))


def fact_source_keys(node: Any) -> set[str]:
    """Return the independent raw/session sources represented by one fact."""
    custom = getattr(node, "custom", None) or {}
    keys = {
        str(value) for value in custom.get("source_raw_memory_ids") or [] if str(value)
    }
    legacy_source = str(getattr(node, "source_raw_memory_id", "") or "")
    if legacy_source:
        keys.add(legacy_source)
    if not keys:
        session_id = str(getattr(node, "session_id", "") or "")
        if session_id:
            keys.add(f"session:{session_id}")
    return keys


def fact_has_unreviewed_sources(node: Any) -> bool:
    """Whether an L2 fact contains provenance added after its last S2 review."""
    custom = getattr(node, "custom", None) or {}
    evidence_count = int(custom.get("s2_evidence_count") or 0)
    reviewed_count = int(custom.get("s2_reviewed_count") or 0)
    if bool(custom.get("s2_forget_reviewed")):
        return False
    source_count = len(fact_source_keys(node))
    reviewed_source_count = custom.get("s2_reviewed_source_count")
    if reviewed_source_count is None:
        # Legacy reviewed nodes did not save a checkpoint. Treat the sources
        # currently present as reviewed; writer UPDATE records this count before
        # appending a future source, so that future source reopens the node.
        reviewed_source_count = source_count if (evidence_count or reviewed_count) else 0
    if source_count > int(reviewed_source_count or 0):
        return True
    return evidence_count == 0 and reviewed_count == 0


def fact_has_unreviewed_holistic_sources(node: Any) -> bool:
    """Whether a fact has raw provenance unseen by holistic therapy review."""
    custom = getattr(node, "custom", None) or {}
    return len(fact_source_keys(node)) > int(
        custom.get("s2_holistic_therapy_reviewed_source_count") or 0
    )


def fact_has_unreviewed_profile_sources(node: Any) -> bool:
    """Whether a fact has raw provenance unseen by profile-fact review."""
    custom = getattr(node, "custom", None) or {}
    reviewed_version = int(custom.get("s2_profile_review_version") or 0)
    reviewed_sources = int(custom.get("s2_profile_reviewed_source_count") or 0)
    return (
        reviewed_version < _S2_PROFILE_FACT_PROMOTION_REVIEW_VERSION
        or len(fact_source_keys(node)) > reviewed_sources
    )


def profile_fact_promotion_enabled() -> bool:
    return _S2_PROFILE_FACT_PROMOTION_ENABLED


def is_user_subject_fact(node: Any) -> bool:
    """Conservatively accept evidence that is explicitly about the user."""
    owner = str(getattr(node, "owner", "") or "").strip().lower()
    if owner in ("agent", "assistant"):
        return False
    content = str(getattr(node, "content", "") or "").strip().lower()
    return bool(re.match(r"^(?:the\s+user|user)(?:\b|\s|[,:])|^\u7528\u6237", content))


def validate_new_schema_evidence(
    op: Dict[str, Any],
    candidate_nodes: Dict[str, Any],
) -> Optional[str]:
    """Require a user-backed, cross-event pattern before creating L6."""
    if not _S2_SCHEMA_EVIDENCE_GATE_ENABLED:
        return None
    if str(op.get("schema_kind") or "behavior_pattern") != "behavior_pattern":
        return "explicit_state_belongs_in_canonical_l2"
    evidence_ids = list(dict.fromkeys(
        str(value) for value in op.get("evidence_list") or [] if str(value)
    ))
    evidence = [candidate_nodes.get(node_id) for node_id in evidence_ids]
    if not evidence_ids or any(node is None for node in evidence):
        return "unknown_evidence"
    user_evidence = [
        node for node in evidence
        if is_user_subject_fact(node)
    ]
    if not user_evidence:
        return "needs_user_evidence"
    source_keys = set().union(*(fact_source_keys(node) for node in user_evidence))
    if len(source_keys) < 2:
        return "needs_two_independent_sources"
    return None


def validate_profile_fact_promotion(
    op: Dict[str, Any],
    candidate_nodes: Dict[str, Any],
) -> Optional[str]:
    """Only a current user-owned L2 candidate may enter the Profile channel."""
    node_id = str(op.get("node_id") or "")
    node = candidate_nodes.get(node_id)
    if node is None:
        return "unknown_fact"
    if not is_user_subject_fact(node):
        return "needs_user_fact"
    if is_explicit_forget_marker(str(getattr(node, "content", "") or "")):
        return "forget_marker_not_profile"
    return None


def build_source_context_groups(
    facts: List[Any],
    *,
    excluded_node_ids: set[str],
    embeddings_by_id: Optional[Dict[str, Any]] = None,
    min_facts: int,
    max_facts: int,
) -> List[Dict[str, Any]]:
    """Group remaining facts by the raw write that produced them.

    Sharing a raw write is context rather than evidence of one semantic pattern.
    The Agent must explicitly account for each candidate fact as used or skipped;
    the raw write is attached only to verify subject and recover omitted details.
    """
    grouped: Dict[str, List[Any]] = {}
    for fact in facts:
        if fact.node_id in excluded_node_ids:
            continue
        source_id = str(getattr(fact, "source_raw_memory_id", "") or "")
        if not source_id:
            continue
        grouped.setdefault(source_id, []).append(fact)

    output: List[Dict[str, Any]] = []
    for source_id, members in grouped.items():
        if len(members) < max(1, min_facts):
            continue
        members = members[:max(1, max_facts)]
        member_vectors = [
            (embeddings_by_id or {}).get(fact.node_id)
            for fact in members
            if (embeddings_by_id or {}).get(fact.node_id) is not None
        ]
        if member_vectors:
            import numpy as np
            centroid = np.mean(np.asarray(member_vectors), axis=0).tolist()
        else:
            centroid = None
        output.append({
            "group_type": "source_context_review",
            "source_raw_memory_id": source_id,
            "source_group_size": len(members),
            "ids": [fact.node_id for fact in members],
            "centroid_text": f"facts from raw memory {source_id}",
            "centroid_embedding": centroid,
            "facts": [
                {
                    "node_id": fact.node_id,
                    "content": fact.content,
                    "layer": fact.layer.value,
                    "owner": getattr(fact, "owner", None),
                    "source_raw_memory_id": source_id,
                    "source_raw_memory_ids": list(
                        (getattr(fact, "custom", None) or {}).get("source_raw_memory_ids") or []
                    ),
                    "session_id": getattr(fact, "session_id", "") or "",
                }
                for fact in members
            ],
        })
    output.sort(key=lambda group: (-group["source_group_size"], group["source_raw_memory_id"]))
    return output


def _cosine_sim_vec(a, b) -> float:
    import numpy as np
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _dedupe_group_indices(
    group_indices: List[int],
    X,
    valid_facts: List,
    sim_threshold: float,
) -> tuple:
    """
    簇内去重：与已保留条目的 embedding 相似度 ≥ threshold 则丢弃。
    Returns:
        (deduped_indices, removed_count)
    """
    if len(group_indices) <= 1:
        return list(group_indices), 0

    kept: List[int] = []
    removed = 0
    for idx in group_indices:
        if not kept:
            kept.append(idx)
            continue
        dup_of = None
        max_sim = 0.0
        for k in kept:
            sim = _cosine_sim_vec(X[idx], X[k])
            if sim > max_sim:
                max_sim = sim
                dup_of = k
        if max_sim >= sim_threshold:
            removed += 1
            logger.info(
                f"[S2-preprocess] cluster dedup: drop {valid_facts[idx].node_id} "
                f"(sim={max_sim:.3f} ≈ {valid_facts[dup_of].node_id})"
            )
        else:
            kept.append(idx)
    return kept, removed


def s2_agent_skip_reason(materials: Dict[str, Any]) -> Optional[str]:
    """
    返回跳过 S2 Agent 的原因；None 表示应运行 Agent。

    - no_facts: 聚类池为空（全已被 schema 引用或 VDB 无 fact）
    - no_clusters: DBSCAN 未形成任何 cluster（散事实，不触发 Agent）
    """
    stats = materials.get("stats") or {}
    if stats.get("total_facts_pool", 0) == 0:
        return "no_facts"
    if stats.get("clusters_found", 0) == 0:
        return "no_clusters"
    return None

# ====================================================================
# System Prompt (dual language)
# ====================================================================

SYSTEM2_AGENT_PROMPT_ZH = """你是一个认知加工 Agent，负责从用户的记忆数据中演化高层认知结构。

## 记忆分层架构

用户的记忆存储在两个系统中：

### VDB（向量数据库）— 你只能读取，不能修改
| 层级 | 说明 |
|------|------|
| L0 BASIC_INFO | 用户基础属性（姓名、年龄、职业等）|
| L1 RAW | 原始对话记录 |
| L2 FACT | 原子事实（从对话中提取的客观事实）|
| L3 SUMMARY | 会话摘要 |
| L4 IDENTITY | 身份画像（性格特征、偏好、习惯）|
| L5 KNOWLEDGE | 领域知识 |

### Graph（知识图谱）— 你是唯一的写入者
| 层级 | 说明 |
|------|------|
| L6 SCHEMA | 行为模式 — 域内、原子化、创建后不可变 |

## L6 Schema 是什么？

Schema 是从多次独立经历归纳出的长期行为模式，包含以下三个要素：

- **Circumstance（场景）**：该模式发生的领域/话题/场景。Schema 必须限定在其场景内，不要跨域泛化。
- **Pattern（模式）**：用户在该场景下的惯常行为、思维方式或行动倾向。
- **Insight（洞察）**：底层心理驱动力或心智模型。

### 内容格式
行为模式用一句话组合三要素：
"当[场景]时，用户[模式]——反映了[洞察]。"

对与情绪、治疗、压力、哀伤或创伤有关的长期模式，不要把具体人生背景压缩成
空泛人格词。优先保留这三部分：
"Trigger/background（持久触发背景） → emotional response（反复情绪反应） → observed coping resource（用户已实际使用的应对资源）。"
只能写入 user-owned 证据中出现的触发背景、反应和应对方式；助手建议不是用户已观察到的应对资源。

### 规则
- **原子化**：一个 Schema 只包含一个模式。两个不同模式 → 两个 Schema。
- **不可变**：Schema 创建后内容永远不变。不要修改已有 Schema 的内容。
- **累积证据**：新事实只是在支持已有表述 → 调 `add_evidence`。如果新证据会让旧画像的当前状态产生实质遗漏、错误或误导，使用 `revise_schema` 创建新版本，让旧版本保留审计但退出 active 检索。
- **域内约束**：不要跨域。跨域抽象由系统的其他流程处理。
- **事实与模式分层**：单条明确身份、健康状态、设备使用、隶属关系、项目或偏好属于 L2 状态，不创建 L6。L6 必须由至少两个独立 raw/session 中的 user-owned 证据支持。
- **主体安全**：同一原始写入可能包含他人资料或示例。只有材料明确归因于当前用户时才创建用户 Schema；否则对该材料不执行任何操作。
- **候选覆盖**：source-context 审查时，每条候选事实必须二选一：用于 `create_schema`/`add_evidence` 的 evidence，或用 `skip_fact` 明确跳过。原始上下文只用于确认主体或补回该事实遗漏的细节，不得据此创建无关 Schema。
- **证据门槛**：创建 L6 必须有 user-owned evidence，且这些证据合计代表至少两个独立 raw/session。
  一个被 S1 合并的 L2 事实可以代表多次独立写入，应检查它的 `source_raw_memory_ids`，不要只数节点。
  助手建议不能作为用户模式的独立证据。

## 工作流程

输入材料分两部分（仅当已形成聚类时才会调用你）：
- **聚类结果（Cluster Results）**：主输入，按语义相似度归组的事实
- **未聚类事实（补充）**：未进入任一聚类的事实，优先级低于聚类结果，酌情审视即可

对于每组事实（无论聚类还是未聚类）：
1. **搜索已有 Schema**：如果已有 Schema 已覆盖该模式，使用 `add_evidence`。只有加入新证据后旧表述会产生实质遗漏、错误或误导时才使用 `revise_schema`。
2. **创建新 Schema**：仅当没有已有 Schema 覆盖时。一句话，三要素。
3. **建立关系**：两个 Schema 主题相关 → 用 `add_edge`（RELATED_TO）。

## 原则

- 所有 Graph 操作必须通过 tools 执行
- Schema 内容创建后不可变——绝不重新创建已存在的 Schema
- 优先 `add_evidence` 而非创建重复节点
- 宁可不创建，也不要创建低质量节点
- 一个 Schema = 一个域内原子模式
- 打标签时优先使用已有标签列表中的标签"""


SYSTEM2_AGENT_PROMPT_EN = """You are a cognitive processing Agent. Your job is to evolve higher-order cognitive structures from a user's raw memory data.

## Memory Architecture

### VDB (Vector Database) — read-only
| Layer | Description |
|-------|-------------|
| L0 BASIC_INFO | User attributes (name, age, occupation) |
| L1 RAW | Original conversation transcripts |
| L2 FACT | Atomic facts extracted from conversations |
| L3 SUMMARY | Session summaries |
| L4 IDENTITY | Identity traits, preferences, habits |
| L5 KNOWLEDGE | Domain knowledge |

### Graph (Knowledge Graph) — you are the sole writer
| Layer | Description |
|-------|-------------|
| L6 SCHEMA | Behavioral patterns — domain-bound, atomic, immutable once created |

## What is an L6 Schema?

A Schema is one cross-event behavioral pattern in the long-term user model and
contains the following three components:


- **Circumstance**: The domain/topic/situation where this pattern is observed. A Schema MUST stay within its circumstance — do NOT generalize across domains. Cross-domain abstraction is handled separately by the system.
- **Pattern**: The user's habitual behavior, thinking style, or action tendency in this circumstance.
- **Insight**: The underlying psychological driver or mental model.

### Content format
Use a single sentence combining all three:
"When [circumstance], the user [pattern] — reflecting [insight]."

For durable emotional, therapy, stress, grief, or trauma-related patterns, do
not erase the specific autobiographical background into a generic personality
label. Prefer an anchored structure that preserves:
"Trigger/background -> recurring emotional response -> observed coping resource."
Only include triggers, responses, and coping resources present in user-owned
evidence. Assistant advice is not an observed user coping resource.

### Rules
- **Atomic**: One pattern per Schema. Two distinct patterns → two Schemas.
- **Immutable**: Once created, content NEVER changes.
- **Evidence only**: New facts that merely support existing wording → call
  `add_evidence`. If new evidence materially changes, corrects, or narrows the
  current durable state, use `revise_schema` so the old version is retained for
  audit but removed from active retrieval.
- **Domain-bound**: Stay within the observed domain. Cross-domain abstraction is handled by a separate system process.
- **Separate facts from patterns**: A single explicit identity, health state,
  equipment use, affiliation, project, or preference belongs in L2 state memory,
  not L6. L6 requires user-owned evidence representing at least two independent
  raw writes or sessions. One reconciled L2 fact may represent multiple writes;
  inspect its source_raw_memory_ids rather than counting fact nodes.
- **Subject safety**: Facts sharing a source chunk may describe other people or
  examples. Create a user Schema only when the material explicitly attributes
  the pattern to the user. Otherwise output no operation for that material.
- **Candidate coverage**: In a source-context review, account for every listed
  candidate fact: either use it as evidence in `create_schema`/`add_evidence`,
  or emit `skip_fact`. Raw context may confirm its subject or restore omitted
  detail, but must not seed unrelated Schemas.
- **Evidence threshold**: Creating L6 requires one or more user-owned evidence
  facts representing at least two independent raw writes or sessions.
  Assistant advice does not establish a user pattern by itself.

### Good vs Bad examples
✅ "When cooking, the user strictly follows recipe steps and precisely measures ingredients — reflecting a need for external structure to manage uncertainty."
✅ "When gaming, the user always checks walkthroughs to achieve 100% completion — driven by low tolerance for incompleteness."
✅ "After an unexpected mentor loss, mortality reminders can bring grief back suddenly; the user pauses to reflect and takes quiet walks to regain steadiness."
❌ "The user approaches music as a medium for creative synthesis and cultural bridge-building, consistently blending diverse musical traditions..." (multiple patterns merged, too broad)
❌ "The user is passionate about many things and values quality." (no circumstance, too vague)
❌ "The user is meaning-driven and resilient." (erases the durable trigger and observed coping evidence)

## Your Workflow

Input materials (you are only invoked when at least one cluster exists):
- **Cluster Results**: Primary input — facts grouped by semantic similarity
- **Unclustered Facts (supplemental)**: Facts not in any cluster; lower priority than cluster results

For each group of facts (clustered or unclustered):
1. **Search existing Schemas**: If an existing Schema already captures this pattern, call `add_evidence`. Use `revise_schema` only when the old wording would become materially incomplete or misleading after adding the new evidence.
2. **Create new Schema**: Only if no existing Schema covers this pattern. One sentence, three components.
3. **Build relationships**: If two Schemas are thematically related, use `add_edge` (RELATED_TO).

## Principles

- All Graph operations MUST go through tools
- Schema content is IMMUTABLE — never recreate what already exists
- Prefer `add_evidence` over creating duplicates
- Prefer not creating over creating low-quality nodes
- One Schema = one atomic pattern in one domain
- When tagging, prefer reusing tags from the existing tags list"""

# backward compat alias
SYSTEM2_AGENT_PROMPT = SYSTEM2_AGENT_PROMPT_ZH


# ====================================================================
# Preprocessor: materials preparation (hardcoded, no LLM)
# ====================================================================

async def prepare_materials(
    user_id: str,
    agent_id: str,
    vector_store: VectorStoreBase,
    graph_store: GraphStoreBase,
    embed_service: EmbedService,
    config: MemoryConfig,
) -> Dict[str, Any]:
    """
    Phase 1: 硬编码预处理 — 聚类 + Graph 正反向搜索 → materials

    Returns:
        {
            "clusters": [{ids, centroid_text, facts: [{node_id, content, layer}]}],
            "graph_forward": [{node_id, content, layer, confidence, evidence_count}],
            "graph_reverse": [{node_id, content, layer, confidence, evidence_vdb_id}],
            "unprocessed_facts": [{node_id, content, layer}],
            "stats": {total_facts, clusters_found, ...}
        }
    """
    # Clustering config（min_samples=4）
    _MIN_FACTS_FOR_INDUCTION = int(os.getenv(
        "MEMORY_S2_MIN_CLUSTER_SIZE",
        "2" if _S2_SCHEMA_EVIDENCE_GATE_ENABLED else "4",
    ))
    _CLUSTERING_THRESHOLD = float(os.getenv("MEMORY_S2_CLUSTER_THRESHOLD", "0.55"))
    # Stage 2：大簇（>12）内再切；阈值比 Stage1 略严即可，0.75 过严难拆出合法子簇
    _REFINE_THRESHOLD = float(os.getenv("MEMORY_S2_REFINE_CLUSTER_THRESHOLD", "0.65"))
    _MAX_CLUSTER_SIZE = 12             # 超过此 size 的 cluster 触发二阶段细分
    _REFINE_MIN_SAMPLES = _MIN_FACTS_FOR_INDUCTION

    # Graph 正向搜索 config
    _GRAPH_SEARCH_TOPK_PER_CLUSTER = 8  # 每个 cluster centroid 召回的 Graph 节点数

    isolation_key = MemoryNode.build_isolation_key(user_id, agent_id or "default")

    # ① VDB: 取 L2_FACT + L4_IDENTITY（可选 L3_SUMMARY）
    _s2_layers = [MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY]
    if _S2_SUMMARY_ENABLED:
        _s2_layers.append(MemoryLayer.L3_SUMMARY)
    all_facts = await vector_store.list_by_user(
        user_id=user_id,
        agent_id=agent_id,
        status_filter=[MemoryStatus.ACTIVE],
        layers=_s2_layers,
    )

    raw_forget_nodes = []
    raw_equipment_nodes = []
    if _S2_FORGET_ENABLED or _S2_CANONICAL_STATE_ENABLED:
        try:
            raw_nodes = await vector_store.list_by_user(
                user_id=user_id,
                agent_id=agent_id,
                status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW],
                layers=[MemoryLayer.L1_RAW],
                allow_raw_layer=True,
            )
            for raw_node in raw_nodes:
                if _S2_FORGET_ENABLED and not bool(
                    (raw_node.custom or {}).get("s2_forget_reviewed")
                ):
                    directives = extract_raw_forget_directives(raw_node.content)
                    if directives:
                        raw_forget_nodes.append((raw_node, directives))
                if _S2_CANONICAL_STATE_ENABLED:
                    raw_equipment_nodes.append(raw_node)
        except Exception as exc:
            logger.warning("[S2-preprocess] raw lifecycle scan failed: %s", exc)

    # ①b 按已审查的 raw provenance 过滤。S1 可能把多次近义事件
    # 合并到同一 L2 节点；节点已被 Schema 引用不代表后续新来源也已审查。
    fresh_facts = []       # evidence_count/review_count = 0 → 待处理
    evidenced_facts = []   # evidence_count >= 1 → 已被引用，排除
    for fact in all_facts:
        if fact_has_unreviewed_sources(fact):
            fresh_facts.append(fact)
        else:
            evidenced_facts.append(fact)

    forget_markers = [
        fact for fact in all_facts
        if _S2_FORGET_ENABLED and is_explicit_forget_marker(fact.content)
        and not bool((fact.custom or {}).get("s2_forget_reviewed"))
    ]
    l2_source_ids = {
        str(getattr(fact, "source_raw_memory_id", "") or "")
        for fact in forget_markers
    }
    for raw_node, directives in raw_forget_nodes:
        if raw_node.node_id in l2_source_ids:
            continue
        forget_markers.append(SimpleNamespace(
            node_id=raw_node.node_id,
            content="\n".join(directives),
            layer=MemoryLayer.L1_RAW,
            custom=raw_node.custom,
            marker_source="l1_raw",
        ))
    forget_marker_ids = {fact.node_id for fact in forget_markers}

    # 聚类池 = 仅全新 facts
    clustering_pool = [
        fact for fact in fresh_facts if fact.node_id not in forget_marker_ids
    ]

    logger.info(
        f"[S2-preprocess] Facts triage: fresh={len(fresh_facts)} "
        f"evidenced_excluded={len(evidenced_facts)} "
        f"(pool={len(clustering_pool)})"
    )

    # ② 两阶段聚类（只对 clustering_pool 做聚类，排除已充分消化的 facts）
    import numpy as np
    clusters = []
    valid_facts = []
    embeddings_by_id: Dict[str, Any] = {}
    try:
        from sklearn.cluster import DBSCAN
        from sklearn.metrics.pairwise import cosine_distances

        # Collect embeddings from clustering_pool (not all_facts)
        embeddings = []
        for fact in clustering_pool:
            emb = fact.embedding
            if isinstance(emb, dict):
                emb = list(emb.values())[0] if emb else None
            if emb and isinstance(emb, (list, np.ndarray)) and len(emb) > 0:
                embeddings.append(emb)
                valid_facts.append(fact)
                embeddings_by_id[fact.node_id] = emb
            else:
                try:
                    emb = await embed_service.embed_queued(fact.content)
                    embeddings.append(emb)
                    valid_facts.append(fact)
                    embeddings_by_id[fact.node_id] = emb
                except Exception:
                    pass

        if len(embeddings) >= _MIN_FACTS_FOR_INDUCTION:
            X = np.array(embeddings)

            # --- Stage 1: 粗分 ---
            dist_matrix = cosine_distances(X)
            db = DBSCAN(eps=1.0 - _CLUSTERING_THRESHOLD, min_samples=_MIN_FACTS_FOR_INDUCTION, metric="precomputed")
            labels = db.fit_predict(dist_matrix)

            stage1_clusters = {}  # label → [indices]
            for i, lbl in enumerate(labels):
                if lbl == -1:
                    continue
                stage1_clusters.setdefault(lbl, []).append(i)

            # --- Stage 2: 对大 cluster 细分 ---
            final_index_groups = []
            for lbl, indices in stage1_clusters.items():
                if len(indices) <= _MAX_CLUSTER_SIZE:
                    final_index_groups.append(indices)
                else:
                    # 子矩阵上用更高 threshold 再跑一次 DBSCAN
                    sub_X = X[indices]
                    sub_dist = cosine_distances(sub_X)
                    sub_db = DBSCAN(
                        eps=1.0 - _REFINE_THRESHOLD,
                        min_samples=_REFINE_MIN_SAMPLES,
                        metric="precomputed",
                    )
                    sub_labels = sub_db.fit_predict(sub_dist)

                    sub_groups = {}
                    noise_indices = []
                    for si, sl in enumerate(sub_labels):
                        if sl == -1:
                            noise_indices.append(indices[si])
                        else:
                            sub_groups.setdefault(sl, []).append(indices[si])

                    for sg in sub_groups.values():
                        final_index_groups.append(sg)

                    # noise 归入最近的子 cluster（或保留为独立 cluster）
                    if noise_indices and sub_groups:
                        # 计算每个 noise 到各子 cluster centroid 的距离，归入最近的
                        sub_centroids = {
                            sl: np.mean(X[[indices[si] for si, sll in enumerate(sub_labels) if sll == sl]], axis=0)
                            for sl in sub_groups
                        }
                        for ni in noise_indices:
                            best_sl = min(sub_centroids, key=lambda sl: float(cosine_distances(
                                X[ni:ni+1], sub_centroids[sl].reshape(1, -1)
                            )[0, 0]))
                            # 找到对应的 final group 并 append
                            for fg in final_index_groups:
                                if any(indices[si] in fg for si, sl in enumerate(sub_labels) if sl == best_sl):
                                    fg.append(ni)
                                    break
                    elif noise_indices:
                        final_index_groups.append(noise_indices)

                    logger.info(
                        f"[S2-preprocess] Refined cluster (size={len(indices)}) "
                        f"→ {len(sub_groups)} sub-clusters + {len(noise_indices)} noise"
                    )

            # --- 构建 cluster 输出（簇内去重 + 最小 N 条，N=_MIN_FACTS_FOR_INDUCTION）---
            dedup_removed_total = 0
            for group_indices in final_index_groups:
                if len(group_indices) < _REFINE_MIN_SAMPLES:
                    continue
                group_indices, n_rm = _dedupe_group_indices(
                    group_indices, X, valid_facts, _S2_CLUSTER_DEDUP_COSINE,
                )
                dedup_removed_total += n_rm
                if len(group_indices) < _MIN_FACTS_FOR_INDUCTION:
                    logger.info(
                        f"[S2-preprocess] cluster dropped after dedup: "
                        f"size={len(group_indices)} < {_MIN_FACTS_FOR_INDUCTION}"
                    )
                    continue
                cluster_facts = [valid_facts[i] for i in group_indices]
                centroid = np.mean(X[group_indices], axis=0)
                dists_to_centroid = [np.linalg.norm(X[i] - centroid) for i in group_indices]
                rep_idx = group_indices[np.argmin(dists_to_centroid)]

                clusters.append({
                    "group_type": "semantic_cluster",
                    "ids": [f.node_id for f in cluster_facts],
                    "centroid_text": valid_facts[rep_idx].content[:100],
                    "centroid_embedding": centroid.tolist(),
                    "facts": [
                        {
                            "node_id": f.node_id,
                            "content": f.content,
                            "layer": f.layer.value,
                            "owner": getattr(f, "owner", None),
                            "source_raw_memory_id": getattr(f, "source_raw_memory_id", "") or "",
                            "source_raw_memory_ids": list(
                                (getattr(f, "custom", None) or {}).get("source_raw_memory_ids") or []
                            ),
                            "session_id": getattr(f, "session_id", "") or "",
                        }
                        for f in cluster_facts
                    ],
                })

            logger.info(
                f"[S2-preprocess] Clustering: {len(valid_facts)} facts → "
                f"{len(stage1_clusters)} stage1 → {len(clusters)} final clusters "
                f"(dedup_removed={dedup_removed_total})"
            )

    except Exception as e:
        logger.warning(f"[S2-preprocess] clustering failed: {e}")

    semantic_clustered_node_ids = {
        fact["node_id"]
        for cluster in clusters
        for fact in cluster.get("facts", [])
    }
    source_groups: List[Dict[str, Any]] = []
    if _S2_SOURCE_GROUPS_ENABLED:
        source_groups = build_source_context_groups(
            valid_facts,
            excluded_node_ids=semantic_clustered_node_ids,
            embeddings_by_id=embeddings_by_id,
            min_facts=_S2_SOURCE_GROUP_MIN_FACTS,
            max_facts=_S2_SOURCE_GROUP_MAX_FACTS,
        )
        for group in source_groups:
            fact_sources = []
            for fact in group.get("facts") or []:
                source_ids = list(fact.get("source_raw_memory_ids") or [])
                legacy_source = str(fact.get("source_raw_memory_id") or "")
                if legacy_source and legacy_source not in source_ids:
                    source_ids.append(legacy_source)
                for source_id in source_ids:
                    pair = (fact, str(source_id))
                    if source_id and pair not in fact_sources:
                        fact_sources.append(pair)
            excerpts = []
            for fact, source_id in fact_sources:
                try:
                    raw_node = await vector_store.get_by_id(source_id)
                    raw_content = str(getattr(raw_node, "content", "") or "")
                    if not raw_content:
                        continue
                    excerpt = select_relevant_raw_excerpt(
                        fact.get("content", ""), raw_content,
                        max_chars=max(512, _S2_SOURCE_RAW_MAX_CHARS // max(1, len(fact_sources))),
                    )
                    excerpts.append({
                        "node_id": fact.get("node_id"),
                        "source_raw_memory_id": source_id,
                        "excerpt": excerpt,
                    })
                except Exception as exc:
                    logger.debug("[S2-preprocess] source raw lookup failed id=%s: %s", source_id, exc)
            if excerpts:
                group["raw_contexts"] = excerpts
        clusters.extend(source_groups)

    # ③ Graph 正向搜索: 用 cluster centroid 向量召回相关 Schema
    #    不再全列所有 Graph 节点 — 只召回与当前 clusters 语义相关的
    graph_forward = []
    seen_node_ids = set()
    try:
        if clusters and graph_store:
            for c in clusters:
                centroid_emb = c.get("centroid_embedding")
                if not centroid_emb:
                    continue
                # 搜索 L6_SCHEMA
                hits = await graph_store.vector_search(
                    query_embedding=centroid_emb,
                    isolation_key=isolation_key,
                    layers=["l6_schema"],
                    limit=_GRAPH_SEARCH_TOPK_PER_CLUSTER,
                    score_threshold=0.3,
                )
                local_graph = []
                for h in hits:
                    if is_internal_profile_chain_hit(h):
                        continue
                    nid = h.get("node_id", "")
                    evidence_refs = await graph_store.get_evidence_vdbrefs(nid)
                    item = {
                        "node_id": nid,
                        "content": h.get("content", ""),
                        "layer": h.get("layer", ""),
                        "confidence": h.get("confidence", 0),
                        "evidence_count": len(evidence_refs),
                        "similarity": round(h.get("score", 0), 4),
                    }
                    local_graph.append(item)
                    if nid not in seen_node_ids:
                        seen_node_ids.add(nid)
                        graph_forward.append(item)
                c["graph_forward"] = local_graph
        logger.info(f"[S2-preprocess] Graph forward: {len(graph_forward)} nodes via centroid search")
    except Exception as e:
        logger.warning(f"[S2-preprocess] graph forward search failed: {e}")

    # ③b 查 Graph 真实节点总数（不依赖 vector_search 结果判断 Graph 是否为空）
    graph_total_schemas = 0
    all_active_schemas: List[Any] = []
    try:
        if graph_store:
            _all_schemas = await graph_store.get_all_nodes(
                isolation_key=isolation_key,
                layer=MemoryLayer.L6_SCHEMA,
                status=MemoryStatus.ACTIVE,
                limit=1000,
            )
            all_active_schemas = [
                node for node in _all_schemas
                if not is_internal_profile_chain_custom(node.custom)
            ]
            graph_total_schemas = len(all_active_schemas)
            logger.info(
                f"[S2-preprocess] Graph actual count: "
                f"schemas={graph_total_schemas} "
                f"(isolation_key={isolation_key})"
            )
    except Exception as e:
        logger.warning(f"[S2-preprocess] graph count query failed: {e}")

    canonical_state_reviews = build_canonical_state_reviews(
        raw_equipment_nodes,
        facts=all_facts,
        active_schemas=all_active_schemas,
    ) if _S2_CANONICAL_STATE_ENABLED else []

    # ④ Graph 反向查找: 从 VDB node_id 找到引用它的 Graph 节点
    graph_reverse = []
    try:
        vdb_ids = [f.node_id for f in valid_facts[:100]]
        graph_reverse = [
            row for row in await graph_store.find_referencing_memories(vdb_ids, limit=50)
            if not is_internal_profile_chain_hit(row)
        ]
    except Exception as e:
        logger.warning(f"[S2-preprocess] graph reverse search failed: {e}")

    # ⑤ 已有 Topic 列表（供 Agent 复用 tag，减少发散）
    existing_tags = []
    try:
        from ..models.memory import MemoryNode as _MN

        topics = await graph_store.get_all_topics(_MN.topic_scope_of(isolation_key))
        existing_tags = [t["name"] for t in topics if t.get("name")]
    except Exception as e:
        logger.debug(f"[S2-preprocess] get_all_topics failed: {e}")

    clustered_node_ids: set = set()
    for c in clusters:
        for f in c.get("facts", []):
            clustered_node_ids.add(f["node_id"])

    unprocessed: List[Dict[str, Any]] = []
    if clusters:
        unprocessed = [
            {"node_id": f.node_id, "content": f.content, "layer": f.layer.value}
            for f in valid_facts
            if f.node_id not in clustered_node_ids
        ]
        if len(unprocessed) > _S2_MAX_UNCLUSTERED_FACTS:
            logger.info(
                f"[S2-preprocess] unclustered facts capped: {len(unprocessed)} → "
                f"{_S2_MAX_UNCLUSTERED_FACTS} (clustered={len(clustered_node_ids)})"
            )
            unprocessed = unprocessed[:_S2_MAX_UNCLUSTERED_FACTS]

    if clusters:
        logger.info(
            f"[S2-preprocess] clusters={len(clusters)} clustered_facts={len(clustered_node_ids)} "
            f"source_groups={len(source_groups)} unclustered_supplement={len(unprocessed)}"
        )
    else:
        logger.info(
            f"[S2-preprocess] no clusters → skip S2 agent "
            f"(fresh_pool={len(valid_facts)}, will not send unprocessed_facts to LLM)"
        )

    return {
        "clusters": clusters,
        "graph_forward": graph_forward,
        "graph_reverse": graph_reverse,
        "existing_tags": existing_tags,
        "unprocessed_facts": unprocessed,
        "forget_markers": [
            {"node_id": fact.node_id, "content": fact.content, "layer": fact.layer.value,
             "marker_source": getattr(fact, "marker_source", "l2_fact")}
            for fact in forget_markers
        ],
        "canonical_state_reviews": canonical_state_reviews,
        "stats": {
            "total_facts_all": len(all_facts),
            "total_facts_pool": len(valid_facts),
            "fresh_facts": len(fresh_facts),
            "evidenced_excluded": len(evidenced_facts),
            "clusters_found": len(clusters),
            "semantic_clusters_found": len(clusters) - len(source_groups),
            "source_groups_found": len(source_groups),
            "clustered_facts_count": len(clustered_node_ids),
            "unclustered_facts_count": len(unprocessed),
            "graph_schemas_recalled": len([g for g in graph_forward if g["layer"] == "l6_schema"]),
            "graph_total_schemas": graph_total_schemas,
            "graph_reverse_hits": len(graph_reverse),
            "existing_tags_count": len(existing_tags),
            "forget_markers_found": len(forget_markers),
            "canonical_state_reviews_found": len(canonical_state_reviews),
            "canonical_state_candidates_found": sum(
                len(review.get("candidates") or []) for review in canonical_state_reviews
            ),
        },
    }


async def prepare_holistic_therapy_materials(
    *,
    user_id: str,
    agent_id: str,
    vector_store: VectorStoreBase,
    graph_store: GraphStoreBase,
) -> Dict[str, Any]:
    """Build one bounded nightly view over user-owned L2 facts across sessions."""
    if not _S2_HOLISTIC_THERAPY_ENABLED:
        return {"enabled": False, "pending": False, "clusters": [], "stats": {}}
    all_facts = await vector_store.list_by_user(
        user_id=user_id,
        agent_id=agent_id,
        status_filter=[MemoryStatus.ACTIVE],
        layers=[MemoryLayer.L2_FACT],
    )
    eligible = [
        fact for fact in all_facts
        if is_user_subject_fact(fact)
        and (getattr(fact, "custom", None) or {}).get("memory_kind") != "canonical_state"
    ]
    pending = [fact for fact in eligible if fact_has_unreviewed_holistic_sources(fact)]
    if not pending:
        return {
            "enabled": True, "pending": False, "clusters": [],
            "stats": {"eligible_facts": len(eligible), "pending_facts": 0},
        }

    def _sort_key(fact):
        value = getattr(fact, "memory_at", None) or getattr(fact, "gmt_created", None)
        return value.timestamp() if hasattr(value, "timestamp") else 0

    pending_ids = {fact.node_id for fact in pending}
    ordered = sorted(eligible, key=lambda fact: (fact.node_id not in pending_ids, -_sort_key(fact)))
    selected = ordered[:max(1, _S2_HOLISTIC_THERAPY_MAX_FACTS)]
    active_schemas = await graph_store.get_all_nodes(
        isolation_key=MemoryNode.build_isolation_key(user_id, agent_id or "default"),
        layer=MemoryLayer.L6_SCHEMA,
        status=MemoryStatus.ACTIVE,
        limit=1000,
    ) if graph_store else []
    active_schemas = [
        node for node in active_schemas
        if not is_internal_profile_chain_custom(node.custom)
    ]
    facts = [{
        "node_id": fact.node_id,
        "content": fact.content,
        "layer": fact.layer.value,
        "owner": getattr(fact, "owner", None),
        "source_raw_memory_id": getattr(fact, "source_raw_memory_id", "") or "",
        "source_raw_memory_ids": list(
            (getattr(fact, "custom", None) or {}).get("source_raw_memory_ids") or []
        ),
        "session_id": getattr(fact, "session_id", "") or "",
    } for fact in selected]
    companion_hints = {}
    for fact in selected:
        if fact.node_id not in pending_ids:
            continue
        anchor_sources = fact_source_keys(fact)
        candidates = []
        for other in selected:
            if other.node_id == fact.node_id:
                continue
            other_sources = fact_source_keys(other)
            combined = anchor_sources | other_sources
            if len(combined) < 2 or not (other_sources - anchor_sources):
                continue
            score = 0.0
            try:
                left = fact.embedding
                right = other.embedding
                if isinstance(left, dict):
                    left = list(left.values())[0] if left else None
                if isinstance(right, dict):
                    right = list(right.values())[0] if right else None
                if left is not None and right is not None:
                    score = _cosine_sim_vec(left, right)
            except Exception:
                score = 0.0
            candidates.append({
                "node_id": other.node_id,
                "similarity": round(float(score), 4),
                "added_raw_source_ids": sorted(other_sources - anchor_sources),
                "combined_raw_source_count": len(combined),
            })
        candidates.sort(key=lambda item: (-item["similarity"], item["node_id"]))
        if candidates:
            companion_hints[fact.node_id] = candidates[:3]
    return {
        "enabled": True,
        "pending": True,
        "holistic_therapy_review": True,
        "clusters": [{
            "group_type": "holistic_therapy_review",
            "centroid_text": "nightly cross-session emotional and therapy consolidation",
            "facts": facts,
        }],
        "graph_forward": [{
            "node_id": node.node_id,
            "content": node.content,
            "layer": node.layer.value,
            "confidence": node.confidence,
            "evidence_count": len(await graph_store.get_evidence_vdbrefs(node.node_id)),
            "similarity": 1.0,
        } for node in active_schemas],
        "graph_reverse": [],
        "existing_tags": [],
        "unprocessed_facts": [],
        "independent_source_companions": companion_hints,
        "stats": {
            "eligible_facts": len(eligible),
            "pending_facts": len(pending),
            "total_facts_all": len(eligible),
            "total_facts_pool": len(selected),
            "fresh_facts": len(pending),
            "evidenced_excluded": len(eligible) - len(pending),
            "clusters_found": 1,
            "clustered_facts_count": len(selected),
            "unclustered_facts_count": 0,
            "graph_total_schemas": len(active_schemas),
            "graph_schemas_recalled": len(active_schemas),
            "holistic_selected_facts": len(selected),
            "holistic_pending_facts": len(pending),
            "holistic_remaining_pending_facts": max(0, len(pending) - len(selected)),
        },
    }


async def run_profile_fact_promotion(
    *,
    user_id: str,
    agent_id: str,
    vector_store: VectorStoreBase,
    config: MemoryConfig,
    forget_markers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Select durable atomic L2 facts for Profile without copying them into L6."""
    if not _S2_PROFILE_FACT_PROMOTION_ENABLED:
        return {"success": True, "enabled": False, "selected": [], "reviewed": 0,
                "prompt_tokens": 0, "completion_tokens": 0}
    if not hasattr(vector_store, "list_by_user"):
        return {"success": True, "enabled": True, "skipped": True,
                "reason": "vector_store_list_unavailable", "selected": [],
                "reviewed": 0, "remaining": 0, "prompt_tokens": 0,
                "completion_tokens": 0}
    facts = await vector_store.list_by_user(
        user_id=user_id,
        agent_id=agent_id,
        status_filter=[MemoryStatus.ACTIVE],
        layers=[MemoryLayer.L2_FACT],
    )
    eligible = [
        fact for fact in facts
        if is_user_subject_fact(fact)
        and (getattr(fact, "custom", None) or {}).get("memory_kind") != "canonical_state"
    ]
    forget_markers = list(forget_markers or [])
    pending = [fact for fact in eligible if fact_has_unreviewed_profile_sources(fact)]
    if forget_markers:
        # A forget request must inspect every currently promoted fact first,
        # including facts whose source count also changed in this cycle.
        promoted = [
            fact for fact in eligible
            if bool((getattr(fact, "custom", None) or {}).get("s2_profile_eligible"))
        ]
        promoted_ids = {fact.node_id for fact in promoted}
        pending = promoted + [fact for fact in pending if fact.node_id not in promoted_ids]
    selected_pool = pending[:max(1, _S2_PROFILE_FACT_PROMOTION_MAX_FACTS)]
    if not selected_pool:
        return {"success": True, "enabled": True, "selected": [], "reviewed": 0,
                "remaining": 0, "prompt_tokens": 0, "completion_tokens": 0}

    from ..agent.llm_provider import LLMProvider
    provider = LLMProvider(config)
    candidates = []
    raw_cache: Dict[str, str] = {}
    per_context_chars = min(1600, max(
        512,
        _S2_PROFILE_FACT_SOURCE_CONTEXT_MAX_CHARS
        // max(1, len(selected_pool) * 2),
    ))
    context_chars_used = 0
    for fact in selected_pool:
        candidate = {"node_id": fact.node_id, "content": fact.content}
        raw_contexts = []
        for source_id in sorted(fact_source_keys(fact))[:3]:
            if source_id.startswith("session:"):
                continue
            if context_chars_used >= _S2_PROFILE_FACT_SOURCE_CONTEXT_MAX_CHARS:
                break
            try:
                if source_id not in raw_cache:
                    raw_node = await vector_store.get_by_id(source_id)
                    raw_cache[source_id] = str(getattr(raw_node, "content", "") or "")
                raw_content = raw_cache[source_id]
                if not raw_content:
                    continue
                excerpt = select_relevant_raw_excerpt(
                    fact.content, raw_content,
                    max_chars=min(
                        per_context_chars,
                        _S2_PROFILE_FACT_SOURCE_CONTEXT_MAX_CHARS - context_chars_used,
                    ),
                )
                if excerpt:
                    raw_contexts.append({
                        "source_raw_memory_id": source_id,
                        "excerpt": excerpt,
                    })
                    context_chars_used += len(excerpt)
            except Exception as exc:
                logger.debug(
                    "[S2-profile] source raw lookup failed id=%s: %s", source_id, exc,
                )
        if raw_contexts:
            candidate["source_context"] = raw_contexts
        candidates.append(candidate)
    system = (
        "You select a very small set of existing atomic memories for a user's "
        "long-term emotional Profile channel. "
        "Do not summarize, rewrite, combine, or infer facts. Select an exact user-owned "
        "L2 fact only when its exact content would materially change future supportive "
        "or therapy-style advice: a durable autobiographical background with ongoing "
        "emotional impact, a recurring burden or fear, an observed coping behavior, or a "
        "value conflict/decision constraint. Do not select identity or occupation facts, "
        "ordinary hobbies or preferences, medical states without an emotional/behavioral "
        "consequence, assistant advice, facts about another person, explicit forget "
        "requests, credentials or sensitive identifiers, one-time logistics, generic "
        "questions, or trivia. Prefer fewer items; "
        "selection is exceptional rather than a summary of the user. Return at most "
        f"{max(0, _S2_PROFILE_FACT_PROMOTION_MAX_SELECTED)} items as JSON only: "
        "A candidate may include `source_context` from the same raw write. Use it only "
        "to recover the meaning and emotional consequence surrounding the exact L2 fact; "
        "it may justify selecting that existing node ID, but must never be treated as a "
        "new fact, rewritten memory, cross-event pattern, or permission to select another ID. "
        "When forget directives are supplied, return any currently promoted fact "
        "that is explicitly targeted in `demote`, and never return it in `selected`. "
        '{"selected":[{"node_id":"...","reason":"..."}],"demote":[{"node_id":"...","reason":"..."}]}'
    )
    try:
        response = await provider.complete_messages(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({
                    "facts": candidates,
                    "forget_directives": [marker.get("content", "") for marker in forget_markers],
                }, ensure_ascii=False)},
            ],
            max_tokens=max(512, int(_evoling_env(
                "MAX_TOKENS", "4096",
            ))),
        )
        parsed = _parse_json_object(response.content or "")
        if parsed is None:
            return {
                "success": False, "error": "invalid profile promotion JSON",
                "selected": [], "reviewed": 0, "remaining": len(pending),
                "prompt_tokens": response.prompt_tokens or 0,
                "completion_tokens": response.completion_tokens or 0,
            }
        by_id = {fact.node_id: fact for fact in selected_pool}
        requested = list(parsed.get("selected") or [])[:max(0, _S2_PROFILE_FACT_PROMOTION_MAX_SELECTED)]
        selected = []
        for item in requested:
            if not isinstance(item, dict):
                continue
            op = {"node_id": str(item.get("node_id") or ""), "reason": item.get("reason", "")}
            if validate_profile_fact_promotion(op, by_id):
                continue
            selected.append(op)
        selected_by_id = {item["node_id"]: item for item in selected}
        demote = []
        for item in list(parsed.get("demote") or []):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "")
            node = by_id.get(node_id)
            if node is None or not bool((getattr(node, "custom", None) or {}).get("s2_profile_eligible")):
                continue
            demote.append({"node_id": node_id, "reason": str(item.get("reason") or "")})
        demote_by_id = {item["node_id"]: item for item in demote}
        updated = 0
        failed_node_ids = []
        for fact in selected_pool:
            custom = dict(getattr(fact, "custom", None) or {})
            source_count = len(fact_source_keys(fact))
            reviewed_source_count = int(custom.get("s2_profile_reviewed_source_count") or 0)
            custom["s2_profile_review_version"] = _S2_PROFILE_FACT_PROMOTION_REVIEW_VERSION
            custom["s2_profile_reviewed_source_count"] = source_count
            if fact.node_id in selected_by_id:
                custom.update({
                    "s2_profile_eligible": True,
                    "s2_profile_reason": str(selected_by_id[fact.node_id].get("reason") or ""),
                    "s2_profile_version": 1,
                })
            elif source_count > reviewed_source_count:
                # Re-evaluation is authoritative for a changed fact. If the
                # selector no longer considers it durable Profile material,
                # remove its previous eligibility instead of leaving stale data.
                custom["s2_profile_eligible"] = False
                custom["s2_profile_demote_reason"] = "not_selected_after_source_update"
            if fact.node_id in demote_by_id:
                custom["s2_profile_eligible"] = False
                custom["s2_profile_demote_reason"] = demote_by_id[fact.node_id]["reason"]
            if await vector_store.update_payload(fact.node_id, {"custom": custom}):
                updated += 1
            else:
                failed_node_ids.append(fact.node_id)
        remaining = max(0, len(pending) - len(selected_pool))
        return {
            "success": not failed_node_ids, "enabled": True,
            "selected": selected, "demoted": demote,
            "reviewed": updated, "remaining": remaining,
            "failed_node_ids": failed_node_ids,
            "prompt_tokens": response.prompt_tokens or 0,
            "completion_tokens": response.completion_tokens or 0,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "selected": [], "reviewed": 0,
                "remaining": len(pending), "prompt_tokens": 0, "completion_tokens": 0}


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    value = str(text or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.S | re.I)
    payload = match.group(1) if match else value
    try:
        result = json.loads(payload)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            result = repair_json(payload, return_objects=True)
            return result if isinstance(result, dict) else None
        except Exception:
            return None


async def run_forget_retirement(
    *,
    forget_markers: List[Dict[str, Any]],
    graph_store: GraphStoreBase,
    tool_executor: System2ToolExecutor,
    config: MemoryConfig,
    isolation_key: str,
) -> Dict[str, Any]:
    """Retire active Schemas targeted by explicit user forgetting markers.

    The LLM can only choose IDs from the current user's active Schema whitelist.
    Tool execution revalidates user scope, active status, and marker semantics.
    """
    if not _S2_FORGET_ENABLED or not forget_markers:
        return {"success": True, "skipped": True, "markers": 0, "tool_calls": [],
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    from ..agent.llm_provider import LLMProvider
    provider = LLMProvider(config)
    system = """You match ONE explicit user forgetting directive to active user Schemas.
Return JSON only: {"retire_schema_ids":["..."],"reason_by_id":{"...":"..."}}.
Retire only a Schema that stores the same USER fact or preference explicitly targeted by the directive.
A broader Schema may be retired only when its defining meaning substantially depends on that forgotten item.
Do not retire a broader commitment because one episode, setback, recommendation, or subtopic was forgotten.
Use only supplied Schema IDs. When uncertain, return an empty list."""
    tool_calls = []
    failures = []
    marker_results = []
    prompt_tokens = completion_tokens = 0
    for marker in forget_markers:
        marker_id = str(marker.get("node_id") or "")
        marker_node = await tool_executor.vector_store.get_by_id(marker_id)
        marker_is_explicit = (
            marker.get("marker_source") == "l1_raw"
            or is_explicit_forget_marker(marker.get("content", ""))
        )
        if marker_node is None or not marker_is_explicit:
            marker_results.append({"marker_id": marker_id, "resolved": True,
                                   "reason": "not_user_scoped_explicit_forget"})
            continue
        embedding = marker_node.embedding
        if isinstance(embedding, dict):
            embedding = list(embedding.values())[0] if embedding else None
        if not embedding:
            embedding = await tool_executor.embed_service.embed_queued(marker.get("content", ""))
        hits = await graph_store.vector_search(
            query_embedding=embedding,
            isolation_key=isolation_key,
            layers=[MemoryLayer.L6_SCHEMA.value],
            limit=8,
            score_threshold=0.0,
        )
        candidates = [
            {"node_id": str(hit.get("node_id") or ""),
             "content": str(hit.get("content") or ""),
             "score": float(hit.get("score") or 0.0)}
            for hit in hits if hit.get("node_id")
        ]
        allowed = {item["node_id"] for item in candidates}
        if not candidates:
            marker_results.append({"marker_id": marker_id, "resolved": True,
                                   "reason": "no_active_schema_candidates"})
            continue
        try:
            response = await provider.complete_messages(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": json.dumps({
                              "forget_marker": marker,
                              "candidate_active_schemas": candidates,
                          }, ensure_ascii=False)}],
                max_tokens=1024,
            )
        except Exception as exc:
            failures.append(f"{marker_id}: LLM failed: {exc}")
            marker_results.append({"marker_id": marker_id, "resolved": False,
                                   "reason": "llm_failed"})
            continue
        prompt_tokens += int(response.prompt_tokens or 0)
        completion_tokens += int(response.completion_tokens or 0)
        parsed = _parse_json_object(response.content or "")
        if parsed is None:
            failures.append(f"{marker_id}: invalid JSON")
            marker_results.append({"marker_id": marker_id, "resolved": False,
                                   "reason": "invalid_json", "response": response.content or ""})
            continue
        selected = []
        for node_id in parsed.get("retire_schema_ids") or []:
            node_id = str(node_id)
            if node_id in allowed and node_id not in selected:
                selected.append(node_id)
        retired = []
        marker_failed = False
        for node_id in selected:
            operation = {
                "node_id": node_id,
                "forget_fact_id": marker_id,
                "reason": str((parsed.get("reason_by_id") or {}).get(node_id)
                              or "explicit user forgetting directive"),
            }
            result = await tool_executor.execute("retire_schema", operation)
            call = {"tool": "retire_schema", "args": operation, "result": result}
            tool_calls.append(call)
            try:
                value = json.loads(result) if isinstance(result, str) else result
            except Exception:
                value = {"error": str(result)}
            if value.get("retired"):
                retired.append(node_id)
            else:
                marker_failed = True
                failures.append(value.get("error") or f"{marker_id}: retirement failed")
        marker_results.append({
            "marker_id": marker_id,
            "resolved": not marker_failed,
            "selected": selected,
            "retired": retired,
            "candidate_schemas": candidates,
            "response": response.content or "",
            "reason": "retired" if retired else "no_match",
        })
    unresolved = [item["marker_id"] for item in marker_results if not item.get("resolved")]
    return {
        "success": not failures,
        "markers": len(forget_markers),
        "tool_calls": tool_calls,
        "failures": failures,
        "marker_results": marker_results,
        "resolved_marker_ids": [item["marker_id"] for item in marker_results if item.get("resolved")],
        "unresolved_marker_ids": unresolved,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


# ====================================================================
# System 2 Agent: LLM + tools
# ====================================================================

async def run_system2_agent(
    materials: Dict[str, Any],
    tool_executor: System2ToolExecutor,
    config: MemoryConfig,
    max_iterations: int = 10,  # kept for backward compat, unused in single-call mode
) -> Dict[str, Any]:
    """
    Phase 2: System 2 Agent — Single LLM call + JSON output + post-process execution.

    Instead of multi-turn tool-calling loop, this version:
    1. Puts all materials (facts + existing graph) into the prompt
    2. Asks LLM to output a JSON array of operations
    3. Parses and executes operations via tool_executor

    Returns:
        {
            "success": bool,
            "tool_calls": [...],
            "agent_reasoning": str,
            "total_tokens": int,
            "elapsed_ms": float,
        }
    """
    from ..agent.llm_provider import LLMProvider

    start = time.time()

    # Build LLMProvider from config
    llm_provider = LLMProvider(config)

    # Detect language from materials content
    lang = _detect_language(materials)
    system_prompt = _build_single_call_system_prompt(lang)
    user_msg = _build_materials_message(materials, lang=lang)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    # Single LLM call (no tools, just JSON output)
    max_tokens = max(512, int(_evoling_env(
        "MAX_TOKENS", str(config.llm.agent_max_tokens or 4000),
    )))
    try:
        response = await llm_provider.complete_messages(
            messages=messages,
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.error(f"[S2-agent] LLM call failed: {e}")
        return {
            "success": False,
            "error": f"LLM call failed: {e}",
            "tool_calls": [],
            "elapsed_ms": (time.time() - start) * 1000,
        }

    total_prompt_tokens = response.prompt_tokens or 0
    total_completion_tokens = response.completion_tokens or 0
    agent_reasoning = response.content or ""

    # Parse JSON operations from response
    operations = _parse_operations_json(agent_reasoning)
    if operations is None:
        logger.warning("[S2-agent] Failed to parse operations JSON from response")
        return {
            "success": False,
            "error_code": 502,
            "failure_kind": "invalid_operations_json",
            "error_message": "System2 response was not a complete JSON operation array",
            "error": "System2 response was not a complete JSON operation array",
            "finish_reason": getattr(response, "finish_reason", "") or "",
            "truncated": (
                (getattr(response, "finish_reason", "") or "") == "length"
                or total_completion_tokens >= max_tokens
            ),
            "tool_calls": [],
            "tool_call_log": [],
            "agent_reasoning": agent_reasoning,
            "messages": messages + [{"role": "assistant", "content": agent_reasoning}],
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "elapsed_ms": (time.time() - start) * 1000,
            "iterations": 1,
            "operations_count": 0,
        }

    source_review = any(
        cluster.get("group_type") == "source_context_review"
        for cluster in materials.get("clusters") or []
    )
    candidate_ids = {
        str(fact.get("node_id") or "")
        for cluster in materials.get("clusters") or []
        for fact in cluster.get("facts") or []
        if fact.get("node_id")
    }
    candidate_nodes = {
        str(fact.get("node_id") or ""): SimpleNamespace(
            node_id=str(fact.get("node_id") or ""),
            content=fact.get("content", ""),
            owner=fact.get("owner"),
            source_raw_memory_id=fact.get("source_raw_memory_id", ""),
            custom={"source_raw_memory_ids": list(fact.get("source_raw_memory_ids") or [])},
            session_id=fact.get("session_id", ""),
        )
        for cluster in materials.get("clusters") or []
        for fact in cluster.get("facts") or []
        if fact.get("node_id")
    }
    handled_candidate_ids = set()
    if materials.get("holistic_therapy_review"):
        allowed = []
        creates = 0
        for op in operations:
            op_type = op.get("op", "")
            if op_type not in ("create_schema", "add_evidence", "skip_fact"):
                continue
            if op_type == "create_schema":
                if creates >= max(0, _S2_HOLISTIC_THERAPY_MAX_SCHEMAS):
                    continue
                creates += 1
                op["schema_kind"] = "behavior_pattern"
            allowed.append(op)
        operations = allowed
    if source_review:
        # The prompt explicitly permits [] when the evidence does not support a
        # durable pattern. In that case the whole one-group review is a valid
        # no-op decision; checkpoint every candidate so the same batch is not
        # retried forever. A non-empty response must still account for each
        # candidate individually, which catches partial omissions.
        if not operations:
            handled_candidate_ids.update(candidate_ids)
        for op in operations:
            op_type = op.get("op", "")
            if op_type in ("create_schema", "add_evidence", "revise_schema"):
                handled_candidate_ids.update(
                    str(node_id) for node_id in op.get("evidence_list") or []
                    if str(node_id) in candidate_ids
                )
            elif op_type == "skip_fact":
                node_id = str(op.get("node_id") or "")
                if node_id in candidate_ids:
                    handled_candidate_ids.add(node_id)
        missing = candidate_ids - handled_candidate_ids
        if missing:
            return {
                "success": False,
                "error_code": 502,
                "failure_kind": "candidate_coverage_incomplete",
                "error_message": f"System2 omitted {len(missing)} candidate fact decisions",
                "error": f"System2 omitted candidate fact decisions: {sorted(missing)}",
                "finish_reason": getattr(response, "finish_reason", "") or "",
                "truncated": False,
                "tool_calls": [],
                "tool_call_log": [],
                "agent_reasoning": agent_reasoning,
                "messages": messages + [{"role": "assistant", "content": agent_reasoning}],
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
                "elapsed_ms": (time.time() - start) * 1000,
                "iterations": 1,
                "operations_count": len(operations),
                "handled_candidate_ids": sorted(handled_candidate_ids),
                "missing_candidate_ids": sorted(missing),
            }

    # Execute operations via tool_executor
    all_tool_calls = []
    state_change_count = 0
    operation_failures = 0
    rejected_candidate_ids = set()
    for op in operations:
        op_type = op.get("op", "")
        try:
            if op_type == "create_schema":
                rejection = validate_new_schema_evidence(op, candidate_nodes)
                if rejection:
                    rejected_candidate_ids.update(
                        str(node_id) for node_id in op.get("evidence_list") or []
                        if str(node_id) in candidate_ids
                    )
                    all_tool_calls.append({
                        "tool": "create_graph_node",
                        "args": op,
                        "result": {"created": False, "rejected": True, "reason": rejection},
                    })
                    continue
                result = await tool_executor.execute("create_graph_node", {
                    "layer": "l6_schema",
                    "content": op.get("content", ""),
                    "evidence_list": op.get("evidence_list", []),
                    "tags": op.get("tags", []),
                    "confidence": op.get("confidence", 0.8),
                    "schema_kind": op.get("schema_kind", "behavior_pattern"),
                    "retrieval_text": op.get("retrieval_text", ""),
                })
                all_tool_calls.append({"tool": "create_graph_node", "args": op, "result": result})
                parsed_result = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed_result, dict) and parsed_result.get("created"):
                    state_change_count += 1
                elif (
                    isinstance(parsed_result, dict)
                    and parsed_result.get("deduplicated")
                    and int(parsed_result.get("evidence_added") or 0) > 0
                ):
                    state_change_count += 1
                elif isinstance(parsed_result, dict) and parsed_result.get("error"):
                    operation_failures += 1

            elif op_type == "add_evidence":
                result = await tool_executor.execute("add_evidence", {
                    "node_id": op.get("node_id", ""),
                    "evidence_list": op.get("evidence_list", []),
                })
                all_tool_calls.append({"tool": "add_evidence", "args": op, "result": result})
                parsed_result = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed_result, dict) and int(parsed_result.get("evidence_added") or 0) > 0:
                    state_change_count += 1
                elif isinstance(parsed_result, dict) and parsed_result.get("error"):
                    operation_failures += 1

            elif op_type == "revise_schema":
                result = await tool_executor.execute("revise_schema", {
                    "node_id": op.get("node_id", ""),
                    "content": op.get("content", ""),
                    "retrieval_text": op.get("retrieval_text", ""),
                    "evidence_list": op.get("evidence_list", []),
                    "tags": op.get("tags", []),
                    "confidence": op.get("confidence", 0.8),
                    "schema_kind": op.get("schema_kind", ""),
                    "reason": op.get("reason", ""),
                })
                all_tool_calls.append({"tool": "revise_schema", "args": op, "result": result})
                parsed_result = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed_result, dict) and parsed_result.get("revised"):
                    state_change_count += 1
                elif isinstance(parsed_result, dict) and parsed_result.get("error"):
                    operation_failures += 1

            elif op_type == "add_edge":
                result = await tool_executor.execute("add_edge", {
                    "source_id": op.get("source_id", ""),
                    "target_id": op.get("target_id", ""),
                    "reason": op.get("reason", ""),
                })
                all_tool_calls.append({"tool": "add_edge", "args": op, "result": result})
                parsed_result = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed_result, dict) and parsed_result.get("error"):
                    operation_failures += 1

            elif op_type == "skip_fact":
                all_tool_calls.append({
                    "tool": "skip_fact",
                    "args": op,
                    "result": {"skipped": True},
                })

            else:
                logger.warning(f"[S2-agent] Unknown operation type: {op_type}")
                all_tool_calls.append({
                    "tool": op_type or "unknown",
                    "args": op,
                    "error": "unknown operation type",
                })
                operation_failures += 1

        except Exception as e:
            logger.warning(f"[S2-agent] Failed to execute op {op_type}: {e}")
            all_tool_calls.append({"tool": op_type, "args": op, "error": str(e)})
            operation_failures += 1

    elapsed_ms = (time.time() - start) * 1000

    logger.info(
        f"[S2-agent] Single-call done: {len(operations)} ops parsed, "
        f"{len(all_tool_calls)} executed, elapsed={elapsed_ms:.0f}ms"
    )

    return {
        "success": operation_failures == 0,
        "tool_calls": all_tool_calls,
        "tool_call_log": tool_executor.tool_call_log,
        "agent_reasoning": agent_reasoning.strip(),
        "messages": messages + [{"role": "assistant", "content": agent_reasoning}],
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "elapsed_ms": elapsed_ms,
        "iterations": 1,
        "operations_count": len(operations),
        "state_change_count": state_change_count,
        "operation_failures": operation_failures,
        "handled_candidate_ids": sorted(handled_candidate_ids) if source_review else None,
        "rejected_candidate_ids": sorted(rejected_candidate_ids),
        "finish_reason": getattr(response, "finish_reason", "") or "",
        "truncated": False,
    }


def _build_single_call_system_prompt(lang: str) -> str:
    """Build system prompt for single-call mode (includes output format instructions)."""
    base = SYSTEM2_AGENT_PROMPT_ZH if lang == "zh" else SYSTEM2_AGENT_PROMPT_EN

    if lang == "zh":
        output_instructions = """

## 输出格式

不要调用 tools。直接输出一个 JSON 数组，包含你决定执行的操作。用 ```json 代码块包裹。

可用操作：

1. **create_schema** — 创建新的 L6 行为 Schema
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "供用户阅读的简洁画像", "retrieval_text": "用于检索的行为与适用场景", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — 给已有 Schema 追加证据（不修改内容）
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — 新证据使已有 Schema 的当前状态发生实质变化时，创建新版本并让旧版本退出默认检索。不要用于可共存的细节补充。
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "更新后的当前画像", "retrieval_text": "更新后的检索文本", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "明确说明旧画像为何会误导"}```

4. **add_edge** — 建立两个 Schema 之间的关系
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "关系原因"}```

5. **skip_fact** — 明确标记某条候选事实已审查但不创建 Schema。仅用于他人事实、临时事件、不能证明用户特征的助手建议，或已被现有 Schema 完整覆盖的事实。
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "不是稳定的用户模式"}```

## 输出约定

1. 输出必须是 JSON 数组，用 ```json 代码块包裹。
2. 如果数据不足以得出可靠结论，输出 `[]`。在 source-context review 中，
   `[]` 表示已审查该组全部候选，且都不足以支持稳定 Schema。
3. 代码块外不要有任何其他文字。
4. evidence_list 中的 id 必须来自材料中提供的 fact node_id。
5. add_evidence / revise_schema 的 node_id 必须来自已有 Graph 节点的 id。
6. 对 source-context 审查，必须让每条候选事实出现在 evidence_list 或 skip_fact 中。"""
    else:
        output_instructions = """

## Output Format

Do NOT call tools. Output a JSON array of operations directly, wrapped in a ```json code block.

Available operations:

1. **create_schema** — Create a new L6 behavioral Schema
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "concise user-facing pattern", "retrieval_text": "behavior plus relevant situations", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Add evidence to an existing Schema (content stays immutable)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Create a new version when new evidence materially changes the current state and retire the old version from active retrieval. Do not use it for compatible detail.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "updated current profile", "retrieval_text": "updated retrieval text", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "why the old wording would mislead"}```

4. **add_edge** — Create a relationship between two Schemas
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "relationship reason"}```

5. **skip_fact** — Explicitly mark a candidate fact as reviewed without creating
   a Schema. Use only for facts about another person, transient events, assistant
   advice that does not establish a user trait, or facts already fully covered.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "not a durable user pattern"}```

## Output Contract

1. Output MUST be a JSON array wrapped in a ```json code block.
2. If data is insufficient for reliable conclusions, output `[]`. In a
   source-context review, `[]` means every candidate in that review was examined
   and none supports a durable Schema.
3. NO text outside the code block.
4. evidence_list IDs must come from the fact node_ids provided in the materials.
5. add_evidence / revise_schema node_id must come from existing Graph node IDs.
6. For a source-context review, every candidate fact must appear in an evidence_list or skip_fact."""

    if _S2_SCHEMA_EVIDENCE_GATE_ENABLED:
        if lang == "zh":
            output_instructions += """

7. L6 只表示跨事件行为模式。create_schema 必须使用 user-owned 证据，且证据合计代表至少两个独立 raw/session。当 `source_raw_memory_ids` 包含多个独立写入时，一个合并后的 L2 事实也可以满足门槛。
8. 单条明确身份、健康状态、设备使用、隶属关系、项目或偏好不要创建 L6；这些由 canonical L2 状态通道维护。"""
        else:
            output_instructions += """

7. L6 represents cross-event behavioral patterns only. create_schema must use user-owned evidence representing at least two independent raw writes or sessions. A single reconciled fact can qualify when source_raw_memory_ids contains multiple independent writes.
8. Do not create L6 for a single explicit identity, health state, equipment use, affiliation, project, or preference. Canonical L2 state projection owns those facts."""

    # Remove the old "All Graph operations MUST go through tools" principle
    base = base.replace("- 所有 Graph 操作必须通过 tools 执行\n", "")
    base = base.replace("- All Graph operations MUST go through tools\n", "")
    # Remove old tool-calling instructions from workflow
    base = base.replace("调 `add_evidence` 添加证据", "使用 add_evidence 操作")
    base = base.replace("call `add_evidence`", "use add_evidence operation")
    base = base.replace("调 `add_evidence`", "使用 add_evidence")
    base = base.replace("用 `add_edge`（RELATED_TO）", "使用 add_edge 操作")
    base = base.replace("use `add_edge` (RELATED_TO)", "use add_edge operation")

    return base + output_instructions


def _parse_operations_json(text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse JSON array of operations from LLM response text."""
    import re

    # Try to find ```json ... ``` block
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
    if match:
        json_str = match.group(1).strip()
    else:
        # Try raw JSON array
        match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', text)
        if match:
            json_str = match.group(0)
        elif '[]' in text:
            return []
        else:
            return None

    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return result
        return None
    except json.JSONDecodeError:
        logger.warning(f"[S2-agent] JSON parse failed ({len(json_str)} chars)")
        logger.debug(f"[S2-agent] unparsable output: {json_str[:200]!r}")
        return None


def _detect_language(materials: Dict[str, Any]) -> str:
    """
    从 materials 的 fact content 检测语言。
    
    扫描所有聚类中的 fact content，计算中文字符占比。
    中文比例 >= 70% → "zh"，否则 → "en"。
    """
    import re
    all_text = ""
    for cluster in materials.get("clusters", []):
        for f in cluster.get("facts", []):
            all_text += f.get("content", "") + " "
    for f in materials.get("unprocessed_facts", []):
        all_text += f.get("content", "") + " "

    if not all_text.strip():
        return "en"

    # 统计中文字符（CJK Unified Ideographs）
    zh_chars = len(re.findall(r'[\u4e00-\u9fff]', all_text))
    # 统计所有非空白字符
    total_chars = len(re.findall(r'\S', all_text))

    if total_chars == 0:
        return "en"

    zh_ratio = zh_chars / total_chars
    lang = "zh" if zh_ratio >= 0.7 else "en"
    logger.debug(f"[S2-agent] language detection: zh_chars={zh_chars} total={total_chars} ratio={zh_ratio:.2f} → {lang}")
    return lang


def _build_materials_message(materials: Dict[str, Any], lang: str = "zh") -> str:
    """将预处理好的 materials 格式化为 Agent 能理解的 user message"""
    is_zh = (lang == "zh")

    parts = [("## 预处理材料\n" if is_zh else "## Preprocessed Materials\n")]

    stats = materials.get("stats", {})
    fresh = stats.get("fresh_facts", 0)
    evidenced = stats.get("evidenced_excluded", 0)
    # 用真实 Graph 节点数判断是否为空（不依赖 vector_search 召回结果）
    graph_total = stats.get("graph_total_schemas", 0)
    recalled_schemas = stats.get("graph_schemas_recalled", 0)

    if is_zh:
        parts.append(f"### 统计\n- VDB 事实总数: {stats.get('total_facts_all', 0)}")
        parts.append(f"- 新鲜事实（待处理）: {fresh}（已被引用排除={evidenced}）")
        parts.append(f"- 聚类数: {stats.get('clusters_found', 0)}")
        parts.append(
            f"- 聚类内事实: {stats.get('clustered_facts_count', 0)}，"
            f"未聚类补充: {stats.get('unclustered_facts_count', 0)}"
        )
        parts.append(f"- Graph Schema 总数: {stats.get('graph_total_schemas', 0)}（本次召回={recalled_schemas}）")
        if graph_total == 0:
            parts.append("- Graph 状态: **空**（首次认知加工）\n")
        else:
            parts.append("")
    else:
        parts.append(f"### Statistics\n- Total VDB facts: {stats.get('total_facts_all', 0)}")
        parts.append(f"- Fresh facts (to process): {fresh} (evidenced_excluded={evidenced})")
        parts.append(f"- Clusters found: {stats.get('clusters_found', 0)}")
        parts.append(
            f"- Facts in clusters: {stats.get('clustered_facts_count', 0)}, "
            f"unclustered supplement: {stats.get('unclustered_facts_count', 0)}"
        )
        parts.append(f"- Graph Schemas total: {stats.get('graph_total_schemas', 0)} (recalled={recalled_schemas})")
        if graph_total == 0:
            parts.append("- Graph status: **EMPTY** (first cognitive processing run)\n")
        else:
            parts.append("")

    # Clusters
    clusters = materials.get("clusters", [])
    clustered_ids = set()
    if clusters:
        parts.append(("### 聚类结果\n" if is_zh else "### Cluster Results\n"))
        for i, c in enumerate(clusters):
            n_facts = len(c['facts'])
            group_type = c.get("group_type", "semantic_cluster")
            if group_type == "holistic_therapy_review":
                label = (
                    f"{n_facts} 条跨会话用户事实；只归纳有证据的长期情绪/治疗模式"
                    if is_zh else
                    f"{n_facts} cross-session user facts; only evidence-backed durable emotional/therapy patterns"
                )
            elif group_type == "source_context_review":
                label = (
                    f"{n_facts} 条候选事实；原始写入仅用于主体与细节核验"
                    if is_zh else
                    f"{n_facts} candidate facts; raw write is only for subject/detail validation"
                )
            else:
                label = f"{n_facts} 条，主题" if is_zh else f"{n_facts} facts, topic"
            parts.append(f"**Cluster {i}** ({label}: {c['centroid_text']})")
            for f in c["facts"]:
                source_ids = list(f.get("source_raw_memory_ids") or [])
                legacy_source = str(f.get("source_raw_memory_id") or "")
                if legacy_source and legacy_source not in source_ids:
                    source_ids.append(legacy_source)
                source_note = f", raw_sources={source_ids}" if source_ids else ""
                parts.append(
                    f"  - [{f['layer']}] {f['content']}  "
                    f"(id={f['node_id']}{source_note})"
                )
                clustered_ids.add(f['node_id'])
            if group_type == "source_context_review" and c.get("raw_contexts"):
                raw_label = "候选事实对应的原始片段" if is_zh else "Relevant raw excerpt per candidate"
                parts.append(f"\n**{raw_label}:**")
                for item in c["raw_contexts"]:
                    parts.append(
                        f"- candidate_id={item.get('node_id')}, "
                        f"raw_source={item.get('source_raw_memory_id', '')}\n"
                        f"{item.get('excerpt', '')}"
                    )
            parts.append("")
        if materials.get("holistic_therapy_review"):
            parts.append(
                (
                    "这是一次有边界的夜间全局整理。最多创建 "
                    f"{_S2_HOLISTIC_THERAPY_MAX_SCHEMAS} 条 anchored therapy Schema。"
                    "优先联合不同 raw/session 中的持久 trigger/background、"
                    "反复 emotional response 和用户已实际使用的 coping resource/decision constraint。"
                    "证据不足时输出 []。\n"
                    if is_zh else
                    "This is one bounded nightly holistic consolidation pass. Create at most "
                    f"{_S2_HOLISTIC_THERAPY_MAX_SCHEMAS} anchored therapy Schemas. "
                    "Prefer joining durable trigger/background, recurring emotional response, "
                    "and observed coping resource or decision constraint across different raw writes/sessions. "
                    "Before create_schema, union raw_sources for its evidence IDs and require at least two "
                    "distinct raw source IDs; two facts from one raw write are one event. "
                    "Output [] when evidence is insufficient.\n"
                )
            )
            companion_hints = materials.get("independent_source_companions") or {}
            if companion_hints:
                parts.append(
                    "**独立来源 companion 提示（只是候选，不是结论）:**"
                    if is_zh else
                    "**Independent-source companion hints (candidates, not conclusions):**"
                )
                for node_id, companions in companion_hints.items():
                    parts.append(
                        f"- {node_id} -> "
                        + ", ".join(
                            f"{item['node_id']} (sim={item['similarity']}, "
                            f"combined_raw_sources={item['combined_raw_source_count']})"
                            for item in companions
                        )
                    )
                parts.append("")
    # 无聚类时不应进入 Agent；此处不输出「未聚类」大块（避免误导读成主输入）
    # Unclustered facts — 仅在有聚类时作为加菜
    all_facts = materials.get("unprocessed_facts", [])
    unclustered = [f for f in all_facts if f["node_id"] not in clustered_ids]
    if clusters and unclustered:
        header = "### 未聚类事实（补充，次要）\n" if is_zh else "### Unclustered Facts (supplemental)\n"
        hint = (
            "以下事实未进入上述任一聚类，可在完成聚类主题加工后酌情审视；优先级低于聚类结果。\n"
            if is_zh
            else "Facts below were not grouped into any cluster above. Review after cluster themes; lower priority than cluster results.\n"
        )
        parts.append(header)
        parts.append(hint)
        for f in unclustered:
            parts.append(f"  - [{f['layer']}] {f['content']}  (id={f['node_id']})")
        parts.append("")

    # Existing graph nodes
    forward = materials.get("graph_forward", [])
    if forward:
        if is_zh:
            parts.append(f"### 相关 Graph 节点（通过 cluster centroid 向量召回，共 {len(forward)} 条）\n")
        else:
            parts.append(f"### Related Graph Nodes (retrieved via cluster centroid search, {len(forward)} hits)\n")
        for g in forward:
            sim = g.get("similarity", "")
            sim_str = f", sim={sim}" if sim else ""
            parts.append(
                f"  - [{g['layer']}] {g['content']} "
                f"(id={g['node_id']}, evidence={g['evidence_count']}{sim_str})"
            )
        parts.append("")
    elif graph_total == 0:
        if is_zh:
            parts.append("### 已有 Graph 节点\n当前 Graph 为空，这是首次认知加工。无需搜索 Graph，直接根据聚类结果创建 Schema。\n")
        else:
            parts.append("### Existing Graph Nodes\nThe Graph is currently EMPTY — this is the first cognitive processing run. Do NOT search the Graph (it will return nothing). Proceed directly to create Schemas from the cluster results.\n")
    else:
        # Graph 有节点但 centroid search 没召回（可能 threshold 太高或语义距离远）
        if is_zh:
            parts.append(
                f"### 已有 Graph 节点\n"
                f"Graph 中共有 {stats.get('graph_total_schemas', 0)} 个 Schema，"
                f"但 centroid 向量召回未命中相关节点。请根据聚类结果判断是否需要创建新 Schema。\n"
            )
        else:
            parts.append(
                f"### Existing Graph Nodes\n"
                f"The Graph has {stats.get('graph_total_schemas', 0)} Schemas, "
                f"but centroid-based vector recall found no matches. Decide based on cluster results whether new Schemas are needed.\n"
            )

    # Reverse hits
    reverse = materials.get("graph_reverse", [])
    if reverse:
        parts.append(("### Graph 反向引用（已有 Schema 引用的 VDB 事实）\n" if is_zh
                       else "### Graph Reverse References (VDB facts referenced by existing Schemas)\n"))
        for r in reverse:
            parts.append(
                f"  - [{r['layer']}] {r['content']} "
                f"(schema_id={r['node_id']}, refs vdb={r.get('evidence_vdb_id', '')})"
            )
        parts.append("")

    # Existing tags（指导 Agent 复用已有 tag，减少发散）
    existing_tags = materials.get("existing_tags", [])
    if existing_tags:
        if is_zh:
            parts.append("### 已有标签（请优先从中选择，避免创建同义标签）\n")
            parts.append(f"  {', '.join(existing_tags)}\n")
        else:
            parts.append("### Existing Tags (prefer reusing these over creating synonyms)\n")
            parts.append(f"  {', '.join(existing_tags)}\n")

    if is_zh:
        parts.append(
            "请分析以上材料，输出操作 JSON 数组。"
            "决定是否需要创建新的 Schema，或为已有 Schema 追加 evidence。"
            "如果数据不足以得出可靠结论，输出空数组 `[]`。"
            "打标签时请优先使用已有标签列表中的标签。"
        )
    else:
        parts.append(
            "Analyze the materials above and output a JSON array of operations. "
            "Decide whether to create new Schemas, or add evidence to existing ones. "
            "If the data is insufficient for reliable conclusions, output an empty array `[]`. "
            "When tagging, prefer reusing tags from the existing tags list above."
        )

    return "\n".join(parts)
